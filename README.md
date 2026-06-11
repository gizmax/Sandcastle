# Sandcastle

**Describe what you want. Go home. Sandcastle ships it.** Production-ready workflow orchestrator for AI agents. 7 AI providers with auto-failover, 22 step types including Claude Managed Agents, 15 agent templates, 4 OCR engines, EU AI Act compliance, and a full-featured dashboard. Define workflows in YAML or let AI design them for you.

[![PyPI](https://img.shields.io/badge/PyPI-v0.33.0-blue?style=flat-square)](https://pypi.org/project/sandcastle-ai/0.33.0/)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL_1.1-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-15000%2B%20passing-brightgreen?style=flat-square)](https://github.com/gizmax/Sandcastle/actions)
[![EU AI Act](https://img.shields.io/badge/EU%20AI%20Act-Compliant-009639?style=flat-square)](https://sandcastle-ai.eu/eu-ai-act/)
[![Website](https://img.shields.io/badge/Website-sandcastle--ai.eu-blue?style=flat-square)](https://sandcastle-ai.eu)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Dashboard-F59E0B?style=flat-square)](https://gizmax.github.io/Sandcastle/)

> **v0.33.0 — "Your Box, Your Brains"** The cloud was always someone else's computer. This release is yours.
>
> NVIDIA put a Grace-Blackwell supercomputer and 128GB of unified memory on a desk. Most teams will run a demo and screenshot the tokens-per-second. We made Sandcastle treat it like home.
>
> - **Spark Mode, zero flags.** Sandcastle auto-detects an NVIDIA DGX Spark and flips to local-first defaults on its own. A startup banner, a dashboard badge, and `GET /api/runtime` all say the same thing: you're running on your own silicon.
> - **Local inference, first-class.** A new `nim/*` provider (NVIDIA NIM, OpenAI-compatible) joins `ollama` and `oMLX` - every one at `region=local`, `$0.00/run`, data-stays-on-box. On a Spark, the default model auto-routes to the local NIM. No token meter. No egress.
> - **Overnight Self-Tune.** The evolution loop can train a task-specific LoRA adapter on a workflow's *own* eval data and route to it. The box doesn't just run your task - it gets better at it, on your data, for $0.
> - **Also:** deterministic cassettes (+ strict mode), shareable run permalinks with a 30-day TTL, `sandcastle run --local`, a growing Template Hub, the 2026-06 model wave (GPT Image 2, Gemini 3.5, GPT-5.2), and a hardening sweep (browser-step SSRF guard, share-token TTL, NIM model-id validation, a sync run endpoint that no longer lies "completed", and a `sandbox_exec` fix for browser dom/computer-use/lightpanda).
>
> <sub>NVIDIA, DGX, and DGX Spark are trademarks of NVIDIA Corporation.</sub>
>
> PyPI: `pip install sandcastle-ai==0.33.0`.
>
> Previous: **v0.32.0 - "Claude Agents Deep Integration"** (May 16, 2026): Memory Stores, Multiagent, Outcomes, Webhooks, Skills Publisher, Trajectory Replay, Agent SDK runtime, Computer Use, MCP Elicitation, Live Agent Reasoning panel.
>

<p align="center">
  <a href="https://gizmax.github.io/Sandcastle/">
    <img src="docs/screenshots/overview.png" alt="Sandcastle Dashboard" width="720" />
  </a>
</p>

<p align="center">
  <a href="https://gizmax.github.io/Sandcastle/"><strong>Try the Live Demo (no backend needed)</strong></a>
</p>

---

## Table of Contents

- [Why Sandcastle?](#why-sandcastle)
- [Start Local. Scale When Ready.](#start-local-scale-when-ready)
- [⚡ Spark Mode — local AI on NVIDIA DGX Spark](#-spark-mode--local-ai-on-nvidia-dgx-spark)
- [Quickstart](#quickstart)
- [MCP Integration](#mcp-integration)
- [Features](#features)
- [Pluggable Sandbox Backends](#pluggable-sandbox-backends)
- [Browser Step - LightPanda & Browserbase](#browser-step---lightpanda--browserbase)
- [Multi-Provider Model Routing](#multi-provider-model-routing)
- [62 Built-in Integrations](#62-built-in-integrations)
- [Workflow Engine](#workflow-engine)
- [22 Step Types](#22-step-types)
- [Managed Agents](#managed-agents)
- [Human Approval Gates](#human-approval-gates)
- [Self-Optimizing Workflows (AutoPilot)](#self-optimizing-workflows-autopilot)
- [Hierarchical Workflows (Workflow-as-Step)](#hierarchical-workflows-workflow-as-step)
- [Policy Engine](#policy-engine)
- [Privacy Router (PII Redaction)](#privacy-router-pii-redaction)
- [Cost-Latency Optimizer](#cost-latency-optimizer)
- [Cost Estimation API](#cost-estimation-api)
- [EU AI Act Compliance](#eu-ai-act-compliance)
- [Tamper-Evident Audit Trail](#tamper-evident-audit-trail)
- [Black Box Flight Recorder](#black-box-flight-recorder)
- [OpenTelemetry](#opentelemetry)
- [Agent Memory](#agent-memory)
- [Evaluations](#evaluations)
- [Directory Input & CSV Export](#directory-input--csv-export)
- [Community Hub](#community-hub)
- [Real-time Event Stream](#real-time-event-stream)
- [Run Time Machine](#run-time-machine)
- [Budget Guardrails](#budget-guardrails)
- [Security](#security)
- [A2A & AG-UI Protocols](#a2a--ag-ui-protocols)
- [Dashboard](#dashboard)
- [API Reference](#api-reference)
- [Multi-Tenant Auth](#multi-tenant-auth)
- [Webhooks](#webhooks)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Development](#development)
- [Acknowledgements](#acknowledgements)
- [License](#license)

---

## Why Sandcastle?

AI agent frameworks give you building blocks - LLM calls, tool use, maybe a graph. But when you start building real products, the glue code piles up fast:

- **"Step A scrapes, step B enriches, step C scores."** - You need workflow orchestration.
- **"Fan out over 50 leads in parallel, then merge."** - You need a DAG engine.
- **"Bill the customer per enrichment, track costs per run."** - You need usage metering.
- **"Alert me if the agent fails, retry with backoff."** - You need production error handling.
- **"Run this every 6 hours and POST results to Slack."** - You need scheduling and webhooks.
- **"A human should review this before the agent continues."** - You need approval gates.
- **"Block the output if it contains PII or leaked secrets."** - You need policy enforcement.
- **"Pick the cheapest model that still meets quality SLOs."** - You need cost-latency optimization.
- **"Use Claude for quality, GPT for speed, Gemini for cost."** - You need multi-provider routing.
- **"Run on E2B cloud, Docker locally, or Cloudflare at the edge."** - You need pluggable runtimes.
- **"Connect to Slack, Jira, GitHub, Salesforce, SAP..."** - You need integrations.
- **"Show me what's running, what failed, and what needs attention."** - You need a dashboard.

Sandcastle handles all of that. Define workflows in YAML, pick your sandbox backend, choose your models, and ship to production.

---

## Start Local. Scale When Ready.

No Docker, no database server, no Redis. Install, run, done.

```bash
pip install sandcastle-ai
sandcastle init        # asks for API keys, picks sandbox backend, writes .env
sandcastle serve       # starts API + dashboard on one port
```

You'll need API keys for your chosen setup:
- **ANTHROPIC_API_KEY** - get one at [console.anthropic.com](https://console.anthropic.com/) (for Claude models)
- **E2B_API_KEY** - get one at [e2b.dev](https://e2b.dev/) (for E2B cloud sandboxes - free tier available)

Or use the `docker` backend (needs Docker installed) or `local` backend (dev only, no sandbox isolation) and skip the E2B key.

Dashboard at `http://localhost:8080`, API at `http://localhost:8080/api`, 127 workflow templates included, 62 integrations ready to connect.

Sandcastle auto-detects your environment. No `DATABASE_URL`? It uses SQLite. No `REDIS_URL`? Jobs run in-process. No S3 credentials? Files go to disk. **Same code, same API, same dashboard** - you just add connection strings when you're ready to scale.

```
 Prototype                 Staging                   Production
 ---------                 -------                   ----------
 SQLite                    PostgreSQL                PostgreSQL
 In-process queue    -->   Redis + arq          -->  Redis + arq
 Local filesystem         Local filesystem          S3 / MinIO
 Single process           Single process            API + Worker + Scheduler
```

| | Local Mode | Production Mode |
|---|---|---|
| **Database** | SQLite (auto-created in `./data/`) | PostgreSQL 16 |
| **Job Queue** | In-process (`asyncio.create_task`) | Redis 7 + arq workers |
| **Storage** | Filesystem (`./data/`) | S3 / MinIO |
| **Scheduler** | In-memory APScheduler | In-memory APScheduler |
| **Setup time** | 30 seconds | 5 minutes |
| **Config needed** | Just API keys | API keys + connection strings |
| **Best for** | Prototyping, solo devs, demos | Teams, production, multi-tenant |

### Ready to scale?

When local mode isn't enough anymore, upgrade one piece at a time. Each step is independent - do only what you need.

**Step 1 - PostgreSQL** (concurrent users, data durability)

```bash
# Install and start PostgreSQL (macOS example)
brew install postgresql@16
brew services start postgresql@16

# Create a database
createdb sandcastle

# Add to .env
echo 'DATABASE_URL=postgresql+asyncpg://localhost/sandcastle' >> .env

# Run migrations
pip install sandcastle-ai  # if not installed yet
alembic upgrade head

# Restart
sandcastle serve
```

Your SQLite data stays in `./data/`. Sandcastle starts fresh with PostgreSQL - existing local runs are not migrated.

**Step 2 - Redis** (background workers, parallel runs)

```bash
# Install and start Redis (macOS example)
brew install redis
brew services start redis

# Add to .env
echo 'REDIS_URL=redis://localhost:6379' >> .env

# Restart API + start a worker in a second terminal
sandcastle serve
sandcastle worker
```

With Redis, workflows run in background workers instead of in-process. You can run multiple workers for parallel execution.

**Step 3 - S3 / MinIO** (artifact storage)

```bash
# Add to .env
echo 'STORAGE_BACKEND=s3' >> .env
echo 'S3_BUCKET=sandcastle-artifacts' >> .env
echo 'AWS_ACCESS_KEY_ID=...' >> .env
echo 'AWS_SECRET_ACCESS_KEY=...' >> .env
# For MinIO, also set: S3_ENDPOINT_URL=http://localhost:9000

# Restart
sandcastle serve
```

**Or skip all that and use Docker:**

```bash
docker compose up -d   # PostgreSQL + Redis + API + Worker, all configured
```

---

## ⚡ Spark Mode — local AI on NVIDIA DGX Spark

Sandcastle's environment auto-detection goes one step further on an **NVIDIA DGX Spark** (or any
Grace-Blackwell host with 110 GB+ unified memory): it turns on **Spark Mode** automatically — no
flags, no config.

- **$0 per run.** Point Sandcastle at a local OpenAI-compatible model server (Ollama, vLLM, or
  NVIDIA NIM) on the Spark's GPU. Inference is tracked at `$0.00`, region `local` — you pay for
  electricity, not tokens.
- **Your data never leaves the box.** Fully local and air-gappable. Pair Spark Mode with the
  `docker` sandbox backend and `data_residency=local` for an offline agent appliance — the engine
  raises at runtime if a step targets a non-local model.
- **Auto-tuned throughput.** Concurrency lifts automatically (5 → 40 workers) to use the Spark's
  headroom for wide `parallel_over` fan-outs.
- **One-glance status.** `sandcastle serve` prints a Spark Mode banner, the dashboard shows a
  `⚡ Spark Mode` badge, and `GET /api/runtime` exposes `spark_mode`.

```bash
# On the Spark: serve a local model (example: Ollama), then point Sandcastle at it
OLLAMA_HOST=http://localhost:11434 sandcastle serve   # Spark Mode auto-detects and engages
```

Override detection anytime with `SANDCASTLE_SPARK_MODE=on|off`. On any non-Spark machine nothing
changes — detection is fail-closed.

> Spark Mode auto-configures Sandcastle for NVIDIA DGX Spark and local NVIDIA GPU inference. It is
> **not** an official NVIDIA certification or endorsement. "NVIDIA", "DGX", and "DGX Spark" are
> trademarks of NVIDIA Corporation, used here only to describe compatibility.

---

## Quickstart

### Production Mode - Docker (recommended)

One command. PostgreSQL, Redis, API server, and background worker - all configured.

```bash
git clone https://github.com/gizmax/Sandcastle.git
cd Sandcastle

# Add your API keys
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
E2B_API_KEY=e2b_...
SANDBOX_BACKEND=e2b
WEBHOOK_SECRET=your-signing-secret
EOF

docker compose up -d
```

That's it. Sandcastle is running at `http://localhost:8080` with PostgreSQL 16, Redis 7, auto-migrations, and an arq background worker.

```bash
docker compose ps       # check status
docker compose logs -f  # tail logs
docker compose down     # stop everything
```

### Production Mode - Manual

If you prefer running without Docker:

```bash
git clone https://github.com/gizmax/Sandcastle.git
cd Sandcastle

cp .env.example .env   # configure all connection strings

uv sync

# Start infrastructure (your own PostgreSQL + Redis)
# Set DATABASE_URL and REDIS_URL in .env

# Run database migrations
uv run alembic upgrade head

# Start the API server (serves API + dashboard on one port)
uv run python -m sandcastle serve

# Start the async worker (separate terminal)
uv run python -m sandcastle worker
```

### Your First Workflow

```bash
# Run a workflow asynchronously
curl -X POST http://localhost:8080/api/workflows/run \
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
curl -X POST http://localhost:8080/api/workflows/run/sync \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "lead-enrichment",
    "input": { "target_url": "https://example.com" }
  }'
```

### Python SDK

Install from PyPI and use Sandcastle programmatically from any Python app:

```bash
pip install sandcastle-ai
```

```python
from sandcastle import SandcastleClient

client = SandcastleClient(base_url="http://localhost:8080", api_key="sc_...")

# Run a workflow and wait for completion
run = client.run("lead-enrichment",
    input={"target_url": "https://example.com"},
    wait=True,
)
print(run.status)          # "completed"
print(run.total_cost_usd)  # 0.12
print(run.outputs)         # {"lead_score": 87, "tier": "A", ...}

# List recent runs
for r in client.list_runs(status="completed", limit=5).items:
    print(f"{r.workflow_name}: {r.status}")

# Stream live events from a running workflow
for event in client.stream(run.run_id):
    print(event)

# Replay a failed step with a different model
new_run = client.fork(run.run_id, from_step="score", changes={"model": "opus"})
```

Async variant available for asyncio apps:

```python
from sandcastle import AsyncSandcastleClient

async with AsyncSandcastleClient() as client:
    run = await client.run("lead-enrichment", input={...}, wait=True)
```

### CLI

The `sandcastle` command gives you full control from the terminal:

```bash
# Interactive setup wizard (API keys, .env, workflows/)
sandcastle init

# Start the server (API + dashboard on one port)
sandcastle serve

# Run a workflow
sandcastle run lead-enrichment -i target_url=https://example.com

# Run and wait for result
sandcastle run lead-enrichment -i target_url=https://example.com --wait

# Check run status
sandcastle status <run-id>

# Stream live logs
sandcastle logs <run-id> --follow

# List runs, workflows, schedules
sandcastle ls runs --status completed --limit 10
sandcastle ls workflows
sandcastle ls schedules

# Manage schedules
sandcastle schedule create lead-enrichment "0 9 * * *" -i target_url=https://example.com
sandcastle schedule delete <schedule-id>

# Community Hub - browse and install templates
sandcastle hub search "lead scoring"
sandcastle hub install competitive-radar
sandcastle hub collections
sandcastle hub install-collection marketing-pro

# Cancel a running workflow
sandcastle cancel <run-id>

# Health check
sandcastle doctor
```

Connection defaults to `http://localhost:8080`. Override with `--url` or `SANDCASTLE_URL` env var. Auth via `--api-key` or `SANDCASTLE_API_KEY`.

### MCP Integration

Sandcastle ships with a built-in [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server. This lets Claude Desktop, Cursor, Windsurf, and any MCP-compatible client interact with Sandcastle directly from the chat interface - run workflows, check status, manage schedules, browse results.

```mermaid
flowchart LR
    Client["Claude Desktop\nCursor / Windsurf"]
    MCP["sandcastle mcp\n(MCP server)"]
    API["localhost:8080\n(sandcastle serve)"]

    Client -->|stdio| MCP -->|HTTP| API
```

Install the MCP extra:

```bash
pip install sandcastle-ai[mcp]
```

#### Available MCP Tools

| Tool | Description |
|------|-------------|
| `run_workflow` | Run a saved workflow by name with optional input data and wait mode |
| `run_workflow_yaml` | Run a workflow from inline YAML definition |
| `get_run_status` | Get detailed run status including all step results |
| `cancel_run` | Cancel a queued or running workflow |
| `list_runs` | List runs with optional status and workflow filters |
| `save_workflow` | Save a workflow YAML definition to the server |
| `create_schedule` | Create a cron schedule for a workflow |
| `delete_schedule` | Delete a workflow schedule |

#### Available MCP Resources

| URI | Description |
|-----|-------------|
| `sandcastle://workflows` | Read-only list of all available workflows |
| `sandcastle://schedules` | Read-only list of all active schedules |
| `sandcastle://health` | Server health status (sandbox backend, DB, Redis) |

#### Client Configuration

**Claude Desktop** - add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sandcastle": {
      "command": "sandcastle",
      "args": ["mcp"],
      "env": {
        "SANDCASTLE_URL": "http://localhost:8080",
        "SANDCASTLE_API_KEY": "sc_..."
      }
    }
  }
}
```

**Cursor** - add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "sandcastle": {
      "command": "sandcastle",
      "args": ["mcp", "--url", "http://localhost:8080"]
    }
  }
}
```

**Windsurf** - add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "sandcastle": {
      "command": "sandcastle",
      "args": ["mcp"]
    }
  }
}
```

The MCP server uses stdio transport (spawned as a child process by the client). It requires a running `sandcastle serve` instance to connect to. Connection is configured via `--url` / `--api-key` CLI args or `SANDCASTLE_URL` / `SANDCASTLE_API_KEY` env vars.

#### What You Can Do from Chat

Once connected, ask your AI assistant to:

- "Run the lead-enrichment workflow for https://example.com"
- "What's the status of my last run?"
- "List all failed runs from today"
- "Create a schedule to run data-sync every day at 9am"
- "Cancel run abc-123"
- "Save this workflow YAML to the server"
- "Show me all available workflows"
- "Check if Sandcastle is healthy"

---

## Features

| Capability | |
|---|---|
| **Pluggable sandbox backends** (E2B, Docker, Local, Cloudflare) | Yes |
| **Multi-provider model routing** (Claude, OpenAI, MiniMax, Google/Gemini, Mistral, Ollama, oMLX) | Yes |
| **⚡ Spark Mode** — auto-detects NVIDIA DGX Spark, local-inference defaults, $0/run, air-gappable | Yes |
| **62 built-in integrations** across 9 categories | Yes |
| **22 step types** (standard, llm, http, code, race, sensor, gate, parse, managed-agent...) | Yes |
| **Zero-config local mode** | Yes |
| **DAG workflow orchestration** | Yes |
| **Parallel step execution** | Yes |
| **Run Time Machine (replay/fork)** | Yes |
| **Budget guardrails** | Yes |
| **Run cancellation** | Yes |
| **Idempotent run requests** | Yes |
| **Persistent storage (S3/MinIO)** | Yes |
| **Webhook callbacks (HMAC-signed)** | Yes |
| **Scheduled / cron agents** | Yes |
| **Retry logic with exponential backoff** | Yes |
| **Dead letter queue with full replay** | Yes |
| **Per-run cost tracking** | Yes |
| **SSE live streaming** | Yes |
| **Multi-tenant API keys** | Yes |
| **Python SDK + async client** | Yes |
| **CLI tool** | Yes |
| **MCP server** (Claude Desktop, Cursor, Windsurf) | Yes |
| **Docker one-command deploy** | Yes |
| **Dashboard with real-time monitoring** | Yes |
| **127 built-in workflow templates** | Yes |
| **118 community templates** (Community Hub) | Yes |
| **Visual workflow builder** | Yes |
| **Directory input (file processing)** | Yes |
| **CSV export per step** | Yes |
| **Human approval gates** | Yes |
| **Self-optimizing workflows (AutoPilot)** | Yes |
| **Hierarchical workflows (workflow-as-step)** | Yes |
| **Policy engine (PII redaction, secret guard)** | Yes |
| **Privacy router (PII redaction, 7 patterns)** | Yes |
| **Pre-run cost estimation** (`POST /runs/estimate`) | Yes |
| **Cost-latency optimizer (SLO-based routing)** | Yes |
| **EU AI Act compliance** (risk classification, transparency reports, Annex IV) | Yes |
| **Tamper-evident audit trail** (SHA-256 hash chain) | Yes |
| **OpenTelemetry instrumentation** (workflow + step spans) | Yes |
| **Browser modes** (LightPanda headless, Browserbase cloud) | Yes |
| **Concurrency control** (rate limiter, semaphores) | Yes |
| **Agent memory** (semantic search, decay, conflict detection) | Yes |
| **Evaluations** (test suites, assertions, pass rate tracking) | Yes |
| **Credential encryption** (Fernet AES-128-CBC) | Yes |
| **API key rotation + IP allowlisting** | Yes |
| **Security headers + CSP** | Yes |
| **Distributed rate limiting** (in-memory + Redis) | Yes |
| **A2A protocol** (Google Agent-to-Agent) | Yes |
| **AG-UI protocol** (CopilotKit SSE streaming) | Yes |
| **Guided onboarding wizard** | Yes |
| **Global search** (runs, workflows, integrations) | Yes |
| **Health insights** (system health score + per-page banners) | Yes |
| **License key system** (community / pro / enterprise tiers) | Yes |

---

## Pluggable Sandbox Backends

Sandcastle uses the **Sandshore runtime** with pluggable backends for agent execution. Each step runs inside an isolated sandbox - choose the backend that fits your needs:

| Backend | Description | Best For |
|---------|-------------|----------|
| **e2b** (default) | Cloud sandboxes via [E2B](https://e2b.dev/) SDK | Production, zero-infra setup |
| **docker** | Local Docker containers with seccomp + CapDrop ALL | Self-hosted, air-gapped environments |
| **local** | Direct subprocess on the host (no isolation) | Development and testing only |
| **cloudflare** | Edge sandboxes via Cloudflare Workers | Low-latency, globally distributed |

```bash
# Set in .env or via sandcastle init
SANDBOX_BACKEND=e2b        # default
SANDBOX_BACKEND=docker     # requires Docker + pip install sandcastle-ai[docker]
SANDBOX_BACKEND=local      # dev only, no isolation
SANDBOX_BACKEND=cloudflare # requires deployed CF Worker
```

All backends share the same `SandboxBackend` protocol - same YAML, same API, same dashboard. Switch backends without changing workflows.

**Docker hardening:** When using the Docker backend, containers run with all capabilities dropped, a seccomp profile restricting syscalls, PID limits (default 100), CPU quotas (default 50%), memory limits (default 512 MiB), and an unprivileged user (1000:1000). All configurable via environment variables.

---

## Browser Step - LightPanda & Browserbase

The built-in `browser` step type supports three modes for web automation:

| Mode | Description | Best For |
|------|-------------|----------|
| **playwright** (default) | Chromium via Playwright (pre-baked in Dockerfile) | General web automation |
| **lightpanda** | 10x faster headless browsing via CDP (no Chromium) | High-throughput scraping |
| **browserbase** | Cloud-hosted browser sessions, zero cold-start | Production scraping, scalable |

```yaml
steps:
  - id: "scrape"
    type: browser
    browser:
      mode: lightpanda          # "playwright" | "lightpanda" | "browserbase"
      url: "https://example.com"
      actions:
        - click: "#load-more"
        - extract: ".results"
```

For Browserbase, set `BROWSERBASE_API_KEY` and `BROWSERBASE_PROJECT_ID` in your environment. LightPanda requires the `lightpanda` binary on `PATH` or set `LIGHTPANDA_PATH`.

---

## Multi-Provider Model Routing

Use different AI providers per step. Claude for quality-critical tasks, cheaper models for simple scoring, or mix providers in a single workflow:

| Model ID | Provider | Runner | Pricing (per 1M tokens) |
|----------|----------|--------|-------------------------|
| `sonnet` | Claude (Anthropic) | Claude Agent SDK | $3 in / $15 out |
| `opus` | Claude (Anthropic) | Claude Agent SDK | $15 in / $75 out |
| `haiku` | Claude (Anthropic) | Claude Agent SDK | $0.80 in / $4 out |
| `openai/codex-mini` | OpenAI | OpenAI-compatible | $0.25 in / $2 out |
| `openai/codex` | OpenAI | OpenAI-compatible | $1.25 in / $10 out |
| `minimax/m2.5` | MiniMax | OpenAI-compatible | $0.30 in / $1.20 out |
| `google/gemini-2.5-pro` | Google (via OpenRouter) | OpenAI-compatible | $4 in / $20 out |
| `omlx/llama-4-scout` | oMLX (local) | OpenAI-compatible | Free (self-hosted) |
| `omlx/mistral-small` | oMLX (local) | OpenAI-compatible | Free (self-hosted) |
| `omlx/gemma-3` | oMLX (local) | OpenAI-compatible | Free (self-hosted) |
| `omlx/qwen-3` | oMLX (local) | OpenAI-compatible | Free (self-hosted) |

```yaml
steps:
  - id: "research"
    model: opus                    # Claude for deep research
    prompt: "Research {input.company} thoroughly."

  - id: "score"
    depends_on: ["research"]
    model: haiku                   # Claude Haiku for cheap scoring
    prompt: "Score this lead 1-100."

  - id: "classify"
    depends_on: ["research"]
    model: openai/codex-mini       # OpenAI for classification
    prompt: "Classify the industry."
```

Automatic failover: if a provider returns 429 or 5xx, Sandcastle retries with the next provider in the fallback chain. Per-key cooldown prevents hammering a failing endpoint.

---

### Universal Advisor

All AI-powered features (workflow generation, evolution, quality evaluation, error explanation)
use a configurable advisor LLM. By default, Sandcastle uses Claude - but you can switch to
any supported provider:

| Provider | Region | Env Vars |
|----------|--------|----------|
| Anthropic (Claude) | US | `ANTHROPIC_API_KEY` |
| Mistral | EU | `MISTRAL_API_KEY` |
| OpenAI | US | `OPENAI_API_KEY` |
| Google (via OpenRouter) | US | `OPENROUTER_API_KEY` |
| MiniMax | US | `MINIMAX_API_KEY` |
| Ollama (local) | Local | None required |
| oMLX (local) | Local | None required |

```bash
# Switch to Mistral (EU data residency)
export SANDCASTLE_ADVISOR_PROVIDER=mistral
export SANDCASTLE_ADVISOR_MODEL=mistral-large-latest
export MISTRAL_API_KEY=your-key

# Switch to local Ollama (100% private, no cloud)
export SANDCASTLE_ADVISOR_PROVIDER=ollama
export SANDCASTLE_ADVISOR_MODEL=llama3.2
```

#### EU Data Residency Mode

Enforce that all AI processing stays within EU borders:

```bash
export DATA_RESIDENCY=eu
```

When active, only EU-region providers (Mistral) and local providers (Ollama, oMLX) are allowed.
Attempts to use US providers will fail with a clear error message.

#### OpenRouter (100+ Models)

Access 100+ models from all major providers through a single API key:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

This enables Google Gemini, Meta Llama, Cohere, and many more through the
unified OpenRouter API. See [openrouter.ai/models](https://openrouter.ai/models)
for the full list.

---

## 62 Built-in Integrations

<p align="center">
  <img src="docs/screenshots/integrations.png" alt="Integrations" width="720" />
</p>

Sandcastle ships with 62 zero-config tool connectors across 9 categories. Each integration is a lightweight JavaScript module that agents can call during workflow execution. Named connections let you wire multiple accounts (e.g. "production-slack" vs "staging-slack"), and all credentials are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).

| Category | Tools |
|----------|-------|
| **Communication** | Slack, Microsoft Teams, Discord, Twilio, SendGrid, Resend, WhatsApp |
| **Project Management** | Jira, Linear, Notion, Airtable, Google Sheets, Figma |
| **CRM** | HubSpot, Salesforce, Zendesk, Intercom |
| **Data** | MongoDB, Snowflake, Supabase, Pinecone, Redis, Database, Google Drive, Qdrant, GCS, Azure Blob |
| **ERP** | SAP, ServiceNow, Helios, ABRA |
| **Payments** | Stripe, Shopify, QuickBooks, Plaid, DocuSign |
| **AI** | OpenAI, Anthropic, ElevenLabs, Langfuse |
| **DevOps** | GitHub, AWS S3, Vercel, Cloudflare Workers, Datadog, PagerDuty |
| **General** | Webhook, Zapier, Calendly, Firecrawl, Tavily, Exa, MCP Bridge, Human Input, Filesystem, Shell, Python Runtime, Code Interpreter, Browser |

```yaml
steps:
  - id: "notify"
    type: notify
    service: slack
    connection: production-slack
    template: "Lead {steps.score.output.company} scored {steps.score.output.score}/100"
```

---

## Workflow Engine

Define multi-step agent pipelines as YAML. Each step can run in parallel, depend on previous steps, pass data forward, and use different models.

### Example: lead-enrichment.yaml

```yaml
name: "Lead Enrichment"
description: "Scrape, enrich, and score leads for sales outreach."
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

### Data Passing Between Steps

When you connect steps with `depends_on`, data flows automatically. You don't need to reference the previous step's output explicitly - Sandcastle injects it as context:

```yaml
steps:
  - id: "research"
    prompt: "Find all EU presidents and return as JSON."

  - id: "enrich"
    depends_on: ["research"]
    prompt: "Add political party and key decisions for each president."
    # Output from "research" is automatically available - no need for {steps.research.output}
```

For fine-grained control, you can still reference specific outputs explicitly using `{steps.STEP_ID.output}` or drill into fields with `{steps.STEP_ID.output.field_name}`:

```yaml
  - id: "score"
    depends_on: ["scrape", "enrich"]
    prompt: |
      Score this lead based on company: {steps.scrape.output.company_name}
      and enrichment: {steps.enrich.output}
```

**Rules:**
- `depends_on` controls execution order **and** data flow
- Unreferenced dependency outputs are appended as context automatically
- Explicitly referenced outputs (`{steps.X.output}`) are placed exactly where you write them
- `{input.X}` references workflow input parameters passed at run time

---

## 22 Step Types

Sandcastle supports 22 step types for building complex workflows beyond simple LLM prompts:

| Phase | Type | Description |
|-------|------|-------------|
| **Core** | `standard` | Default LLM prompt execution in sandbox |
| **Core** | `llm` | Direct LLM call without sandbox overhead |
| **Core** | `http` | HTTP request (GET/POST/PUT/DELETE) with response parsing |
| **Core** | `code` | Execute code snippets (Python/JS) in sandbox |
| **Core** | `condition` | Branch workflow based on expression evaluation |
| **Core** | `classify` | Route to different branches based on LLM classification |
| **Core** | `loop` | Iterate over a list, executing sub-steps for each item |
| **Core** | `parse` | Extract text from PDF, DOCX, XLSX, PPTX, CSV - no LLM cost |
| **Advanced** | `race` | Run parallel branches, take the first to complete |
| **Advanced** | `sensor` | Poll a URL until a condition is met (webhook alternative) |
| **Advanced** | `gate` | Multi-strategy approval (human, LLM judge, quorum) |
| **Advanced** | `transform` | Jinja2 template rendering for data transformation |
| **Advanced** | `notify` | Send alerts via Slack, Teams, email, or webhook |
| **Advanced** | `delegate` | Spawn a sub-workflow and collect results |
| **Advanced** | `openclaw` | Delegate to an autonomous OpenClaw agent |
| **Advanced** | `managed-agent` | Delegate to Anthropic Managed Agents (15 built-in templates) |
| **Built-in** | `approval` | Human approval gate with timeout and auto-action |
| **Built-in** | `sub_workflow` | Execute another workflow as a step |
| **Built-in** | `browser` | Web automation with Playwright, LightPanda, or Browserbase |
| **Built-in** | `composio` | 500+ business app actions via Composio integration |
| **Built-in** | `report` | Generate PDF reports with charts, KPIs, and callouts |

```yaml
steps:
  - id: "check-api"
    type: sensor
    sensor:
      url: "https://api.example.com/status"
      interval_seconds: 30
      timeout_seconds: 300
      condition: "response.status == 'ready'"

  - id: "fastest-wins"
    type: race
    depends_on: ["check-api"]
    branches:
      - id: "openai-branch"
        model: openai/codex-mini
        prompt: "Analyze {steps.check-api.output}"
      - id: "claude-branch"
        model: haiku
        prompt: "Analyze {steps.check-api.output}"

  - id: "format"
    type: transform
    depends_on: ["fastest-wins"]
    template: |
      ## Analysis Report
      **Result:** {{ steps['fastest-wins'].output.summary }}
      **Generated:** {{ now() }}
```

---

## Managed Agents

Delegate complex tasks to Anthropic Claude Managed Agents running in isolated cloud containers with full tool access (bash, file I/O, web browsing). Three configuration modes let you go from zero-config to fully customized:

### Templates (Quickstart)

15 built-in agent templates across 6 categories:

| Category | Template | Role |
|----------|----------|------|
| **Research** | `researcher` | Web research with citations and source verification |
| **Research** | `seo_specialist` | SEO audit, keywords, meta tags, structured data |
| **Development** | `coder` | Python development - writes and runs code |
| **Development** | `reviewer` | Code review, security audit, best practices |
| **Development** | `tester` | Pytest tests, coverage, fixtures |
| **Development** | `devops` | Dockerfiles, CI/CD, deployment scripts |
| **Development** | `designer` | HTML/CSS/Tailwind prototypes, SVG |
| **Data** | `analyst` | Pandas, matplotlib, statistical analysis |
| **Data** | `sql_expert` | SQL optimization, schema design, migrations |
| **Data** | `scraper` | Web scraping, BeautifulSoup, data extraction |
| **Content** | `writer` | Content writing, proofreading, editing |
| **Content** | `translator` | Translation with cultural context, 90+ languages |
| **Business** | `legal_analyst` | Contract analysis, risk identification, clause extraction |
| **Business** | `financial_analyst` | Financial models, charts, forecasting |
| **Operations** | `project_manager` | Project breakdown, Gantt charts, status reports |

Advanced features: `output_format` (json/files/markdown), `shared_files` between agents, `fallback_template` on failure.

```yaml
# A) Template - one line to configure
steps:
  - id: deep-research
    type: managed-agent
    managed_agent_config:
      agent_template: researcher
      message: "Research {input.topic} and provide a comprehensive report with citations"

# B) Describe - AI designs the agent from natural language
  - id: custom-analyst
    type: managed-agent
    managed_agent_config:
      describe: "Data analyst who downloads CSVs, cleans data with pandas, generates matplotlib charts"
      message: "Analyze this dataset: {steps.fetch.output}"

# C) Explicit - full control over every parameter
  - id: security-audit
    type: managed-agent
    managed_agent_config:
      agent_id: auto
      model: claude-sonnet-4-6
      system_prompt: "You are a security auditor. Find vulnerabilities."
      tools_enabled: [bash, read, grep, glob]
      packages: [bandit, safety]
      network_access: false
      message: "Audit this codebase for vulnerabilities"
```

**When to use managed-agent vs llm:** Use `managed-agent` when the task needs tool execution (running code, browsing the web, reading/writing files). Use `llm` for pure text generation where no tools are needed. Managed agents cost more but can autonomously solve multi-step problems.

Requires `ANTHROPIC_API_KEY` environment variable.

---

## Self-Describing Workflows

Every step can declare `responsibility`, `source_hint`, `owner`, and `added_date` metadata so your workflows stay understandable as they grow. Three new CLI commands support this: `sandcastle describe` prints a human-readable summary of any workflow, `sandcastle lint` flags missing metadata and structural issues, and `sandcastle owners` lists who owns each step. The audit trail and dashboard are enriched with this metadata automatically.

## Dynamic Context (context_query)

LLM steps can now fetch relevant context before execution using the `context_query` field. Four sources are supported: Agent Memory (semantic search over stored knowledge), web (Tavily search), local files (keyword search across project files), and custom (arbitrary shell command output). Retrieved context is injected as `{context}` in prompts, giving your LLM steps fresh, relevant information without manual prompt engineering.

## Streaming CLI (sandcastle run --stream)

The `sandcastle run --stream` flag enables live terminal output with color-coded status: green for completed steps, yellow for running, red for failed. Each step shows timing, cost, and responsibility. Pressing Ctrl+C detaches from the stream and lets the workflow continue in background. Three additional commands ship in this release: `sandcastle describe`, `sandcastle lint`, and `sandcastle owners`.

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

## Privacy Router (PII Redaction)

The Privacy Router runs automatically on all step inputs and outputs, detecting and redacting sensitive data before it reaches logs, storage, or downstream steps.

**7 built-in PII patterns:** email addresses, phone numbers, SSNs, credit card numbers, IP addresses, IBANs, and dates of birth.

**Two modes:**
- `redact` - replaces matched values with `[REDACTED]` tokens
- `audit_only` - logs the detection without modifying the data

Configure per-workflow in YAML or globally via environment variables:

```yaml
# Per-workflow configuration
privacy:
  mode: redact            # "redact" | "audit_only"
  patterns:               # optional: restrict to specific patterns
    - email
    - credit_card
    - ssn
  exclude_steps:          # optional: skip privacy check on these steps
    - internal-analysis
```

```bash
# Per-server configuration (env vars)
PRIVACY_MODE=redact
PRIVACY_PATTERNS=email,phone,ssn,credit_card,ip,iban,dob
```

The Privacy Router integrates with the audit trail - every redaction event is logged with run ID, step ID, and matched pattern type (not the matched value).

---

## Cost Estimation API

Estimate the cost of a workflow before running it. The `/runs/estimate` endpoint parses the workflow YAML, resolves model assignments per step (including classify/gate overrides), and returns a per-step and total cost breakdown based on average token usage.

```bash
curl -X POST http://localhost:8080/api/runs/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "lead-enrichment",
    "input": { "target_url": "https://example.com" }
  }'
```

```json
{
  "data": {
    "valid": true,
    "validation_errors": [],
    "estimated_cost_usd": 0.18,
    "steps": [
      { "id": "scrape",  "model": "sonnet", "estimated_cost_usd": 0.06 },
      { "id": "enrich",  "model": "sonnet", "estimated_cost_usd": 0.09 },
      { "id": "score",   "model": "haiku",  "estimated_cost_usd": 0.03 }
    ]
  }
}
```

The `valid` field indicates whether the workflow passes validation. Invalid workflows still return an estimate but include a disclaimer that the figure may be unreliable. Falls back to sonnet pricing for unknown models.

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

## EU AI Act Compliance

Sandcastle includes built-in support for EU AI Act requirements. Classify workflows by risk level, enforce compliance policies, generate transparency reports, and produce Annex IV technical documentation.

### Risk Classification

Set `risk_level` in any workflow YAML:

```yaml
name: "Loan Assessment"
risk_level: high          # "minimal" | "limited" | "high" | "unacceptable"
description: "Automated credit scoring workflow."
steps:
  - id: "evaluate"
    prompt: "Evaluate loan application: {input.application}"
```

Risk level behavior:
- `unacceptable` - blocked at submission, workflow will not run
- `high` - requires human approval when `COMPLIANCE_MODE=eu_ai_act` is set
- `limited` / `minimal` - logged and included in transparency reports, no gate

### Compliance Mode

```bash
# Enable EU AI Act enforcement in .env
COMPLIANCE_MODE=eu_ai_act
```

Check active compliance features:

```bash
GET /api/compliance/status
```

### Transparency Reports

Every completed run exposes an Article 13 transparency report:

```bash
GET /api/runs/{run_id}/transparency-report
```

Returns: AI models used, human oversight steps, policy violations, risk level, and input prompt log (if `risk_level: high`).

### Annex IV Generator

Generate a technical documentation stub for EU AI Act Annex IV:

```bash
GET /api/workflows/{workflow_name}/annex-iv
```

Returns a structured document covering system description, intended purpose, risk level, human oversight measures, and technical characteristics.

### Emergency Stop

Cancel all running and queued workflows globally (e.g. in response to a compliance incident):

```bash
POST /api/admin/emergency-stop
```

Sets a Redis/in-memory flag checked by the executor. All active runs are cancelled immediately.

---

## Tamper-Evident Audit Trail

Every significant event in Sandcastle is appended to a tamper-evident audit log. Each entry is chained to the previous using SHA-256 hashes (`entry_hash` covers the event content; `prev_hash` is the hash of the preceding entry), making retroactive modification detectable.

**Hooked events:** workflow submission, step start/complete/fail, approval decisions, admin actions (emergency stop, key rotation, bulk delete), and policy violations.

### Audit Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/audit` | Paginated audit log (filterable by run, event type, date) |
| `GET` | `/api/runs/{id}/audit` | Audit events for a specific run |
| `GET` | `/api/audit/verify/{id}` | Verify hash chain integrity for an event |

```bash
# Verify the hash chain for a run's audit events
curl http://localhost:8080/api/audit/verify/run_abc123
# Returns: { "valid": true, "chain_length": 12, "broken_at": null }
```

---

## Black Box Flight Recorder

A flight recorder for AI agents: every run becomes a signed, tamper-evident, replayable audit
artifact. Built for teams that need to show exactly what their AI did, with cryptographic proof
that the record has not been altered since the run - the kind of traceable, replayable record
the EU AI Act expects from production AI systems.

Each recorded step is appended to a hash chain (`record_hash` = SHA-256 over the canonical
record + `prev_hash`), and the chain head is signed with your audit key (HMAC-SHA256). Editing,
removing, or reordering a single record breaks every hash after it - and verification points to
the exact record that changed. The same file is a deterministic cassette, so any audited run
can be replayed offline, byte-for-byte, at zero cost.

```bash
# .env
COMPLIANCE_MODE=black_box        # enable the flight recorder
DATA_RESIDENCY=local             # required: the audit trail never leaves your infra
SANDCASTLE_AUDIT_KEY=<secret>    # signs every chain head
```

With `COMPLIANCE_MODE=black_box`, Sandcastle enforces at runtime:

- **Every run is recorded** - a signed cassette is written for each run, automatically
- **Every chain head is signed** - runs refuse to start without an audit key
- **Local data residency** - runs refuse to start unless `DATA_RESIDENCY=local`
- **Attested run API** - `GET /api/runs/{id}` returns `signed: true` plus the chain head hash

Verify any run or cassette file offline:

```bash
sandcastle audit verify <run-id>            # resolves the run's cassette under the data dir
sandcastle audit verify run.cassette.json   # or verify a file directly
# PASS - hash chain intact, every record verifies
# FAIL - first broken record: chain index 2 (record was modified)
```

Or from Python:

```python
from sandcastle.engine.cassette import verify_cassette

result = verify_cassette("run.cassette.json")
print(result.status, result.chain_head, result.first_broken_index)
```

Signatures embed their algorithm (`hmac-sha256` today), so asymmetric backends like Ed25519
can be added without invalidating existing cassettes.

---

## OpenTelemetry

Sandcastle emits OTLP traces for every workflow run and step, including cost, token counts, and duration as span attributes.

Install the optional extra:

```bash
pip install sandcastle-ai[otel]
```

Configure via environment variables:

```bash
OTEL_ENABLED=true
OTEL_ENDPOINT=http://localhost:4318    # OTLP HTTP collector endpoint
OTEL_SERVICE_NAME=sandcastle           # optional, defaults to "sandcastle"
```

Each workflow run creates a root span. Each step creates a child span with attributes: `sandcastle.step.id`, `sandcastle.step.model`, `sandcastle.step.cost_usd`, `sandcastle.step.input_tokens`, `sandcastle.step.output_tokens`, `sandcastle.step.duration_ms`.

Compatible with any OTLP-capable backend: Jaeger, Grafana Tempo, Honeycomb, Datadog, etc.

---

## Agent Memory

Sandcastle includes a built-in memory system that gives agents persistent context across workflow runs. Memories are semantically searchable, automatically enriched with keywords and tags, and decay over time based on relevance.

- **Write admission control** - Scores incoming memories (0.0-1.0) based on length, structure, and novelty. Rejects low-value or duplicate information automatically.
- **Semantic search** - Sentence-transformer embeddings for finding relevant memories by meaning, not just keywords.
- **Automatic decay** - Memories expire after a configurable TTL (default 90 days). Relevance scores decrease linearly with age.
- **Conflict detection** - Flags new memories that overlap >40% with existing entries, preventing duplication.
- **Auto-enrichment** - Extracts keywords, auto-tags (issue, preference, insight, data), and timestamps.
- **Optional graph backend** - Neo4j support for relationship-aware memory queries.

```python
# Configured via environment variables
MEMORY_BACKEND=local        # "local" (SQLite + embeddings) or "cloud" (Mem0)
MEMORY_GRAPH_ENABLED=false  # Enable Neo4j graph backend
MEMORY_MAX_AGE_DAYS=90      # TTL for memory decay (0 = keep forever)
MEMORY_ADMIT_THRESHOLD=0.3  # Minimum quality score for admission
```

---

## Evaluations

<p align="center">
  <img src="docs/screenshots/evaluations.png" alt="Evaluations" width="720" />
</p>

Run test suites against your workflows to measure quality over time. Define assertions (exact match, contains, schema validation, LLM judge), track pass rates, cost per test, and duration. Trend charts show quality regression before it hits production.

---

## Directory Input & CSV Export

Process files from a directory and export results to CSV - all configured in YAML. The workflow builder includes a directory browser and CSV export toggle per step.

### Directory input

Mark a step as directory-aware and Sandcastle adds a `directory` field to the workflow's input schema. Users provide a path at run time, and the agent reads files from that directory.

```yaml
input_schema:
  required: ["directory"]
  properties:
    directory:
      type: string
      description: "Path to directory"
      default: "~/Documents"

steps:
  - id: "analyze"
    prompt: |
      Read every file in {input.directory} and summarize the key findings.
```

### CSV export

Any step can export its output to CSV. Two modes:

- **new_file** - each run creates a timestamped file (e.g. `report_20260217_143022.csv`)
- **append** - all runs append rows to a single file, perfect for ongoing data collection

```yaml
steps:
  - id: "extract"
    prompt: "Extract all contacts from {input.directory}."
    csv_output:
      directory: ./output
      mode: new_file
      filename: contacts    # optional, defaults to step ID

  - id: "score"
    depends_on: ["extract"]
    prompt: "Score each contact for sales potential."
    csv_output:
      directory: ./output
      mode: append          # all runs land in one file
      filename: scores
```

Works with any output shape - dicts become columns, lists of dicts become rows, plain text goes into a `value` column. Directories are created automatically.

---

## Document Parsing

Parse PDF, DOCX, XLSX, and CSV files directly in your workflows:

```yaml
steps:
  - id: extract
    type: parse
    prompt: "{input.document}"
    parse_config:
      output: markdown
      pages: "1-10"

  - id: analyze
    prompt: "Analyze: {steps.extract.output.text}"
    depends_on: [extract]
```

Supported formats: PDF (with optional OCR), DOCX, XLSX, PPTX, CSV.
Install parsing support: `pip install sandcastle-ai[parse]`

**OCR engines:** `pymupdf` (fast text extraction), `chandra` (scanned docs, handwriting), `glm-ocr` (0.9B vision-language model via Ollama, 94.6% accuracy, runs 100% locally). In `auto` mode Sandcastle tries pymupdf first, then glm-ocr (if Ollama is available), then Chandra.

PDFs are also auto-parsed when uploaded as workflow inputs (if pymupdf is installed).

---

### Batch Run

Run any workflow on hundreds of items in parallel. Upload a CSV or JSON array, set max parallelism, and Sandcastle fans out into concurrent runs with progress tracking and aggregate cost reporting.

```bash
curl -X POST http://localhost:8080/api/workflows/lead-enrichment/batch \
  -H "X-API-Key: $KEY" \
  -d '{"items": [{"url": "https://acme.com"}, {"url": "https://beta.io"}], "max_parallel": 10}'
```

### Auto-Update

Sandcastle checks for updates and can upgrade itself. CLI: `sandcastle update`. Dashboard: one-click update with rollback. Supports stable/beta/pinned channels, blackout windows, and enterprise approval policies.

### Schedule Monitor

Dashboard page showing all cron schedules at a glance: last run status, next run countdown, success rate, pause/resume controls. No more guessing which scheduled workflow failed overnight.

### Workflow Version Diff

Every workflow edit is versioned. Compare any two versions side-by-side with YAML diff highlighting. See exactly what changed, when, and roll back if needed.

### Activity Feed

Real-time activity stream on the dashboard: workflow runs, failures, edits, API key events. Know what happened without digging through logs.

---

## Community Hub

<p align="center">
  <img src="docs/screenshots/template-browser.png" alt="Template Browser" width="720" />
</p>

Sandcastle ships with 127 built-in workflow templates. The [Community Hub](https://sandcastle-ai.eu/hub) adds 118 more from the community - curated collections for marketing, sales, DevOps, and more.

| Category | Built-in Templates |
|----------|-----------|
| **Marketing** | Blog to Social, SEO Content, Email Campaign, Competitor Analysis, Ad Copy Generator, Competitive Radar, Content Atomizer |
| **Sales** | Lead Enrichment, Proposal Generator, Meeting Recap, Lead Outreach |
| **Support** | Ticket Classifier, Review Sentiment |
| **HR** | Job Description, Resume Screener |
| **Legal** | Contract Review |
| **Product** | Release Notes, Data Extractor |
| **Foundational** | Summarize, Translate, Research Agent, Chain of Thought, Review and Approve |

```bash
# Browse the community hub
sandcastle hub search "lead scoring"

# Install a community template
sandcastle hub install competitive-radar

# Install a whole collection
sandcastle hub collections
sandcastle hub install-collection marketing-pro

# Publish your own template
sandcastle hub publish my-workflow.yaml
```

---

## Real-time Event Stream

Sandcastle provides a global SSE endpoint for real-time updates across the entire system:

```bash
# Connect to the global event stream
curl -N http://localhost:8080/api/events
```

The dashboard uses this stream to power live indicators showing connection status, toast notifications for run completion and failure, and instant updates across all pages. Event types include:

- `run.started` - A workflow run was queued and started executing
- `run.completed` - A run finished successfully with outputs
- `run.failed` - A run failed (all retries exhausted)
- `step.started`, `step.completed`, `step.failed` - Per-step progress events
- `dlq.new` - A new item landed in the dead letter queue

No polling, no delays - every state change is pushed the moment it happens.

---

## Run Time Machine

Every completed step saves a checkpoint. When something goes wrong - or you just want to try a different approach - you don't have to start over.

**Replay** - Re-run from any step. Sandcastle loads the checkpoint from just before that step and continues execution. All prior steps are skipped, their outputs restored from the checkpoint. Costs only what's re-executed.

**Fork** - Same as replay, but you change something first. Swap the model from Haiku to Opus. Rewrite the prompt. Adjust max_turns. The new run branches off with your changes and Sandcastle tracks the full lineage.

```bash
# Replay from the "enrich" step
curl -X POST http://localhost:8080/api/runs/{run_id}/replay \
  -H "Content-Type: application/json" \
  -d '{ "from_step": "enrich" }'

# Fork with a different model
curl -X POST http://localhost:8080/api/runs/{run_id}/fork \
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
curl -X POST http://localhost:8080/api/workflows/run \
  -d '{ "workflow": "enrichment", "input": {...}, "max_cost_usd": 0.50 }'
```

---

## Security

Sandcastle includes multiple layers of security hardening for production deployments:

### Credential Encryption

All integration credentials are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256). Set `CREDENTIAL_ENCRYPTION_KEY` in your environment - without it, credentials are stored as plaintext (backwards compatible for local dev).

### API Key Rotation

Rotate API keys with zero downtime. The old key remains valid during a configurable grace period (default 24 hours), giving clients time to switch over.

```bash
# Rotate a key
curl -X POST http://localhost:8080/api/api-keys/{key_id}/rotate

# Response includes new key + old key expiry time
```

### IP Allowlisting

Restrict API keys to specific IP ranges. Supports IPv4 and IPv6 CIDR notation. Empty list means allow all.

```bash
curl -X PUT http://localhost:8080/api/api-keys/{key_id}/allowlist \
  -d '{ "cidrs": ["10.0.0.0/8", "2001:db8::/32"] }'
```

### Security Headers

All responses include hardened security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Permissions-Policy` denying camera/microphone/geolocation/payment. Dashboard pages get a full Content-Security-Policy (configurable as report-only via `CSP_REPORT_ONLY`).

### Rate Limiting

Sliding-window rate limiting on expensive endpoints (workflow execution). Per-tenant when authenticated, per-IP when anonymous. Two backends:

- **In-memory** (default) - single-process, zero deps
- **Redis** (auto-detected from `REDIS_URL`) - distributed, uses sorted sets with atomic check+increment

### Secret Scrubber

Step outputs and log messages are automatically scrubbed for leaked credentials before storage. The scrubber catches:
- Credential URLs (`postgres://user:pass@host`, `redis://:pass@host`)
- PEM private key blocks (RSA, EC, DSA, ENCRYPTED)
- Azure `AccountKey=` values
- Compound env-style secrets (`aws_secret_access_key=...`)
- JSON-quoted secrets (`"password": "value"`)

The scrubber is idempotent (double-scrubbing produces the same output) and uses a two-layer defense: PEM regex runs first, then token-based regex.

### Privacy Router

The Privacy Router (see [Privacy Router (PII Redaction)](#privacy-router-pii-redaction)) provides a second layer of output sanitization, detecting and redacting PII (emails, phones, SSNs, credit cards, IPs, IBANs, dates of birth) across all step outputs. Configurable per-workflow or globally.

### Docker Sandbox Hardening

When using the Docker backend, every container runs with:
- All capabilities dropped (`CapDrop: ALL`)
- Seccomp profile restricting dangerous syscalls
- PID limit (default 100, configurable)
- CPU quota (default 50%, configurable)
- Memory limit (default 512 MiB, configurable)
- Unprivileged user (1000:1000)
- Auto-remove on exit

---

## A2A & AG-UI Protocols

### A2A (Agent-to-Agent)

Sandcastle implements [Google's A2A protocol](https://google.github.io/A2A/) for agent interoperability. Any A2A-compatible agent can discover and call Sandcastle workflows via standard JSON-RPC 2.0.

- **Discovery:** `GET /.well-known/agent.json` - returns capabilities, skills, and supported methods
- **Execute:** `POST /a2a` - `tasks/send` to run workflows, `tasks/get` to poll status, `tasks/cancel` to abort
- **Status mapping:** Internal states (queued, running, completed, failed, awaiting_approval) map to A2A standard states

### AG-UI (Agent-User Interaction)

Sandcastle streams workflow execution via the [CopilotKit AG-UI protocol](https://docs.copilotkit.ai/), enabling real-time UI integration with compatible frontends.

- **Endpoint:** `GET /api/agui/stream/{run_id}` (Server-Sent Events)
- **Events:** `run_started`, `run_finished`, `run_error`, `step_started`, `text_message`, `tool_call_start`, `tool_call_end`, `state_delta`
- **Timeout:** 10-minute streaming window with 1s polling interval

---

## Dashboard

Sandcastle ships with a full-featured dashboard built with React, TypeScript, and Tailwind CSS v4. Dark and light theme, real-time updates via SSE, global search across runs/workflows/integrations, and zero configuration - just open `http://localhost:8080` after `sandcastle serve`. For frontend development, run `cd dashboard && npm run dev`.

### Overview

KPI cards, 30-day run trends, cost breakdown per workflow, recent runs at a glance. The Lighthouse health score surfaces actionable insights when something needs attention - DLQ items, critical violations, failing runs, unconfigured integrations.

<p align="center">
  <img src="docs/screenshots/overview.png" alt="Overview" width="720" />
</p>

<details>
<summary>Dark mode</summary>
<p align="center">
  <img src="docs/screenshots/overview-dark.png" alt="Overview - Dark Mode" width="720" />
</p>
</details>

### Runs

Filterable run history with status badges, duration, cost per run. Bulk actions (cancel, delete) with multi-select. Contextual banner warns when success rate drops below 90%.

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

Live view of a running workflow showing parallel step execution. Steps with a pulsing blue dot are currently executing inside sandboxes.

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

Visual drag-and-drop editor for building workflows. Add steps, connect dependencies, configure models and timeouts, then preview the generated YAML. Collapsible advanced sections for retry logic, CSV export, AutoPilot, approval gates, policy rules, and SLO optimizer - all reflected in the YAML preview. Directory input with a server-side file browser. Editing an existing workflow loads its steps and edges into the canvas.

<p align="center">
  <img src="docs/screenshots/workflow-builder.png" alt="Workflow Builder" width="720" />
</p>

### Integrations

62 tool connectors across 9 categories. Each tool shows connection status, named connections, and a configuration panel. Contextual banner highlights tools that are available but not yet configured.

<p align="center">
  <img src="docs/screenshots/integrations.png" alt="Integrations" width="720" />
</p>

### Schedules

Cron-based scheduling with human-readable descriptions, enable/disable toggle, and inline edit. Click "Edit" to change the cron expression or toggle a schedule without leaving the page.

<p align="center">
  <img src="docs/screenshots/schedules.png" alt="Schedules" width="720" />
</p>

### API Keys

Create, view, rotate, and deactivate multi-tenant API keys. IP allowlisting per key. Key prefix shown in monospace, full key revealed only once on creation with a copy-to-clipboard flow.

<p align="center">
  <img src="docs/screenshots/api-keys.png" alt="API Keys" width="720" />
</p>

### Dead Letter Queue

Failed steps that exhausted all retries land here. Retry triggers a full re-run. Resolve marks the issue as handled. Sidebar badge shows unresolved count. Contextual banner surfaces when items need attention.

<p align="center">
  <img src="docs/screenshots/dead-letter.png" alt="Dead Letter Queue" width="720" />
</p>

### Approval Gates

Pending, approved, rejected, and skipped gates with filterable tabs. Each pending approval has Approve, Reject, and Skip buttons. Configurable timeouts auto-resolve approvals if nobody responds.

<p align="center">
  <img src="docs/screenshots/approvals.png" alt="Approval Gates" width="720" />
</p>

<details>
<summary>Expanded with request data</summary>

Click any approval to expand it and see the full request data the agent produced. If `allow_edit` is enabled, reviewers can modify the data before approving.

<p align="center">
  <img src="docs/screenshots/approvals-detail.png" alt="Approval Gate Detail" width="720" />
</p>
</details>

### AutoPilot - Self-Optimizing Workflows

A/B test different models, prompts, and configurations on any workflow step. Stats cards show active experiments, total samples, average quality improvement, and cost savings.

<p align="center">
  <img src="docs/screenshots/autopilot.png" alt="AutoPilot Experiments" width="720" />
</p>

<details>
<summary>Expanded with variant comparison</summary>

Each variant shows sample count, average quality score, cost, and duration. The "BEST" badge highlights the current leader.

<p align="center">
  <img src="docs/screenshots/autopilot-detail.png" alt="AutoPilot Variant Comparison" width="720" />
</p>
</details>

### Evaluations

Test suite management with pass rate tracking, cost per test, assertion details, and trend analysis over time.

<p align="center">
  <img src="docs/screenshots/evaluations.png" alt="Evaluations" width="720" />
</p>

### Policy Violations

Every policy trigger logged with severity, action taken, and full context. Filter by severity (Critical, High, Medium, Low). Color-coded badges for blocked, redacted, flagged, or logged actions.

<p align="center">
  <img src="docs/screenshots/violations.png" alt="Policy Violations" width="720" />
</p>

<details>
<summary>Expanded with trigger details</summary>

Click any violation to see what pattern matched, what was detected, and what action was taken. Includes links to the originating run and step.

<p align="center">
  <img src="docs/screenshots/violations-detail.png" alt="Violation Detail" width="720" />
</p>
</details>

### Cost-Latency Optimizer

Real-time view of model routing decisions. Stats cards show total decisions, average confidence, top model, and estimated savings. Budget pressure indicators pulse red when spending approaches limits.

<p align="center">
  <img src="docs/screenshots/optimizer.png" alt="Cost-Latency Optimizer" width="720" />
</p>

<details>
<summary>Expanded with alternatives and SLO config</summary>

Full alternatives table with scores and the SLO configuration that drove the selection.

<p align="center">
  <img src="docs/screenshots/optimizer-detail.png" alt="Optimizer Decision Detail" width="720" />
</p>
</details>

### Settings

Editable configuration sections for connections (API keys for providers), security (auth settings), budget (cost limits), webhooks, and system settings (log levels, depth limits, storage). License tier display.

<p align="center">
  <img src="docs/screenshots/settings.png" alt="Settings" width="720" />
</p>

### System Health

Service status checks (Runtime, Redis, Database), uptime tracking, and quick stats (workflows, runs, templates, API keys).

<p align="center">
  <img src="docs/screenshots/system.png" alt="System Health" width="720" />
</p>

### Onboarding Wizard

First-run guided setup that walks new users through API key configuration, sandbox backend selection, and first workflow execution.

<p align="center">
  <img src="docs/screenshots/onboarding.png" alt="Onboarding Wizard" width="720" />
</p>

---

## API Reference

### Workflows

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/workflows` | List available workflows |
| `POST` | `/api/workflows` | Save new workflow YAML |
| `POST` | `/api/workflows/run` | Run workflow async (returns run_id) |
| `POST` | `/api/workflows/run/sync` | Run workflow sync (blocks until done) |

### Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/runs` | List runs (filterable by status, workflow, date, tenant) |
| `GET` | `/api/runs/{id}` | Get run detail with step statuses |
| `GET` | `/api/runs/{id}/stream` | SSE stream of live progress |
| `POST` | `/api/runs/{id}/cancel` | Cancel a running workflow |
| `POST` | `/api/runs/{id}/replay` | Replay from a specific step |
| `POST` | `/api/runs/{id}/fork` | Fork from a step with overrides |
| `POST` | `/api/runs/estimate` | Pre-run cost estimation with per-step breakdown |
| `GET` | `/api/runs/{id}/audit` | Audit events for a specific run |
| `GET` | `/api/runs/{id}/transparency-report` | EU AI Act Article 13 transparency report |

### Schedules

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/schedules` | Create cron schedule |
| `GET` | `/api/schedules` | List all schedules |
| `PATCH` | `/api/schedules/{id}` | Update schedule (cron, enabled, input) |
| `DELETE` | `/api/schedules/{id}` | Delete schedule |

### Dead Letter Queue

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/dead-letter` | List failed items |
| `POST` | `/api/dead-letter/{id}/retry` | Retry failed step (full replay) |
| `POST` | `/api/dead-letter/{id}/resolve` | Mark as resolved |

### Approval Gates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/approvals` | List approvals (filterable by status) |
| `GET` | `/api/approvals/{id}` | Get approval detail with request data |
| `POST` | `/api/approvals/{id}/approve` | Approve (optional edit + comment) |
| `POST` | `/api/approvals/{id}/reject` | Reject and fail the run |
| `POST` | `/api/approvals/{id}/skip` | Skip step and continue workflow |

### AutoPilot

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/autopilot/experiments` | List experiments |
| `GET` | `/api/autopilot/experiments/{id}` | Experiment detail with samples + stats |
| `POST` | `/api/autopilot/experiments/{id}/deploy` | Manually deploy a winning variant |
| `POST` | `/api/autopilot/experiments/{id}/reset` | Reset experiment |
| `GET` | `/api/autopilot/stats` | Savings and quality overview |

### Policy Engine

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/violations` | List policy violations (filterable) |
| `GET` | `/api/violations/stats` | Violation stats by severity, policy, day |
| `GET` | `/api/runs/{id}/violations` | Violations for a specific run |

### Optimizer

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/optimizer/decisions` | List routing decisions |
| `GET` | `/api/optimizer/decisions/{run_id}` | Decisions for a specific run |
| `GET` | `/api/optimizer/stats` | Model distribution, confidence, savings |

### Integrations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tools` | List all available integrations with status |
| `GET` | `/api/tools/{id}` | Get tool detail and configuration |
| `PUT` | `/api/tools/{id}/credentials` | Save encrypted credentials for a tool |
| `DELETE` | `/api/tools/{id}/credentials` | Remove credentials |

### Evaluations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/eval/suites` | List evaluation test suites |
| `GET` | `/api/eval/runs` | List eval runs with results |
| `GET` | `/api/eval/stats` | Pass rates, cost stats, trends |

### API Keys

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/api-keys` | Create API key (returns plaintext once) |
| `GET` | `/api/api-keys` | List active keys (prefix only) |
| `DELETE` | `/api/api-keys/{id}` | Deactivate key |
| `POST` | `/api/api-keys/{id}/rotate` | Rotate key with grace period |
| `PUT` | `/api/api-keys/{id}/allowlist` | Set IP allowlist (CIDR notation) |

### Audit Trail

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/audit` | Paginated audit log (filterable by run, type, date) |
| `GET` | `/api/audit/verify/{id}` | Verify SHA-256 hash chain integrity for an event |

### Compliance (EU AI Act)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/compliance/status` | Active compliance features and mode |
| `GET` | `/api/workflows/{name}/annex-iv` | Generate Annex IV technical documentation stub |
| `POST` | `/api/admin/emergency-stop` | Cancel all running/queued workflows globally |

### Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/templates` | List all built-in workflow templates |
| `GET` | `/api/templates/{id}` | Get template detail with YAML |

### Evolution

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/evolution/start` | Start workflow evolution |
| `GET` | `/api/evolution/{name}/status` | Evolution status |
| `POST` | `/api/evolution/{name}/accept` | Accept best variant |

### Agent Memory

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/memory/add` | Store memory entry |
| `POST` | `/api/memory/search` | Semantic search |
| `DELETE` | `/api/memories/{id}` | Delete memory |

### Advisor

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/advisor/status` | Current provider config |
| `POST` | `/api/advisor/configure` | Switch provider |
| `GET` | `/api/advisor/cost-estimate` | Cost comparison |

### Events & System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/events` | Global SSE stream (run, step, DLQ events) |
| `GET` | `/api/health` | Health check (sandbox backend, DB, Redis) |
| `GET` | `/api/runtime` | Current mode info (database, queue, storage, license) |
| `GET` | `/api/stats` | Aggregated stats and cost trends |

### Protocols

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/.well-known/agent.json` | A2A agent discovery |
| `POST` | `/a2a` | A2A JSON-RPC 2.0 endpoint |
| `GET` | `/api/agui/stream/{run_id}` | AG-UI SSE streaming |

All responses follow the envelope format: `{ "data": ..., "error": null }` or `{ "data": null, "error": { "code": "...", "message": "..." } }`.

---

## Multi-Tenant Auth

Sandcastle supports strict multi-tenant isolation via API keys. Every API key maps to a `tenant_id`, and all queries are automatically scoped. Keys are hashed with HMAC-SHA256 and a server-side pepper - plaintext keys are never stored.

```bash
# Create an API key
curl -X POST http://localhost:8080/api/api-keys \
  -d '{ "tenant_id": "acme-corp", "name": "Production" }'
# Returns: { "data": { "key": "sc_abc123...", "key_prefix": "sc_abc12" } }

# Use it
curl http://localhost:8080/api/runs -H "X-API-Key: sc_abc123..."
# Only sees runs belonging to acme-corp
```

Toggle with `AUTH_REQUIRED=true|false` (default: false for local dev). When enabled, all endpoints except `/api/health` require a valid API key. Expired keys return 401 `KEY_EXPIRED`. Blocked IPs return 403 `IP_BLOCKED`.

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

```mermaid
flowchart TD
    App["Your App"] -->|"POST /api/workflows/run"| API["Sandcastle API\n(FastAPI)"]
    A2A["A2A Agents"] -->|"POST /a2a"| API
    AGUI["AG-UI Clients"] -->|"GET /api/agui/stream"| API

    API --> Engine["Workflow Engine\n(DAG executor, 22 step types)"]

    Engine --> Standard["Standard Steps"]
    Engine --> Sub["Sub-Workflow Steps\n(recursive execution)"]
    Engine --> Advanced["Advanced Steps\n(race, sensor, gate, transform, notify, delegate)"]

    Standard --> Sandshore["Sandshore Runtime\n(pluggable backends)"]
    Sub --> Child["Child Engine"]
    Advanced --> Sandshore
    Child --> SandshoreChild["Sandshore (child)"]

    Sandshore --> E2B["E2B\n(cloud)"]
    Sandshore --> Docker["Docker\n(seccomp + caps)"]
    Sandshore --> Local["Local\n(subprocess)"]
    Sandshore --> CF["Cloudflare\n(edge)"]
    SandshoreChild --> E2B2["Sandbox (child)"]

    E2B --> Execution
    Docker --> Execution
    Local --> Execution
    CF --> Execution
    E2B2 --> Merge

    Execution["Parallel Execution\n62 integrations"] --> Provider["Multi-Provider Router\nClaude / OpenAI / MiniMax / Gemini"]

    Provider --> Gate{"Approval\nGate?"}

    Gate -->|"Pause"| Review["Approve / Reject / Skip"]
    Gate -->|"Continue"| AutoPilot

    Review --> AutoPilot["AutoPilot\nA/B test variants"]
    AutoPilot --> Policy["Policy Engine\nPII redact / block / alert"]
    Policy --> Optimizer["SLO Optimizer\nRoute to best model"]
    Optimizer --> Memory["Agent Memory\nsemantic search + decay"]

    Memory --> Merge((" "))

    Merge --> LocalMode["Local Mode\nSQLite / In-process queue / Filesystem"]
    Merge --> ProdMode["Production Mode\nPostgreSQL / Redis (arq) / S3"]
    Merge --> BothModes["Both Modes\nWebhooks / SSE Stream / APScheduler"]
```

- **Local mode** - auto-created SQLite, in-process asyncio queue, and local filesystem. Zero dependencies.
- **Production mode** - PostgreSQL (runs, API keys, approvals, experiments, violations, routing decisions, checkpoints), Redis via arq (job queue, cancel flags), and S3/MinIO for persistent artifact storage.
- **Both modes** - HMAC-signed webhooks, SSE event streaming, and APScheduler for cron-based scheduling.

### Tech Stack

| Component | Local Mode | Production Mode |
|-----------|------------|-----------------|
| API Server | Python 3.12, FastAPI, Uvicorn | Python 3.12, FastAPI, Uvicorn |
| Database | SQLite + aiosqlite | PostgreSQL 16 + asyncpg + Alembic |
| Job Queue | In-process (asyncio) | Redis 7 + arq |
| Scheduler | APScheduler (in-memory) | APScheduler (in-memory) |
| Storage | Local filesystem | S3 / MinIO |
| Agent Runtime | Sandshore (E2B / Docker / Local / Cloudflare) | Sandshore (E2B / Docker / Local / Cloudflare) |
| Model Providers | Claude, OpenAI, MiniMax, Google/Gemini | Claude, OpenAI, MiniMax, Google/Gemini |
| Integrations | 62 tools, 9 categories | 62 tools, 9 categories |
| Security | Fernet encryption, HMAC auth, rate limiting | + Redis rate limiting, seccomp, IP allowlists |
| Dashboard | React 18, TypeScript, Vite, Tailwind CSS v4 | React 18, TypeScript, Vite, Tailwind CSS v4 |
| DAG Visualization | @xyflow/react | @xyflow/react |
| Charts | Recharts | Recharts |
| SDK | `SandcastleClient` (httpx, sync + async) | `SandcastleClient` (httpx, sync + async) |
| CLI | argparse (zero deps) | argparse (zero deps) |
| Deployment | `python -m sandcastle serve` | Docker + docker-compose |

---

## Configuration

All configuration via environment variables or `.env` file. Run `sandcastle init` for an interactive setup wizard. Mode is auto-detected based on `DATABASE_URL` and `REDIS_URL`:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...
E2B_API_KEY=e2b_...            # required for E2B backend

# Sandbox backend: "e2b" (default) | "docker" | "local" | "cloudflare"
SANDBOX_BACKEND=e2b
MAX_CONCURRENT_SANDBOXES=5     # rate limiter for parallel execution

# Docker backend (only if SANDBOX_BACKEND=docker)
# DOCKER_IMAGE=sandcastle-runner:latest
# DOCKER_URL=                  # empty = local Docker socket
# DOCKER_SECCOMP_PROFILE=      # path to seccomp JSON (built-in default used if empty)
# DOCKER_PIDS_LIMIT=100
# DOCKER_CPU_PERIOD=100000
# DOCKER_CPU_QUOTA=50000
# DOCKER_MEMORY_LIMIT=536870912  # 512 MiB

# Cloudflare backend (only if SANDBOX_BACKEND=cloudflare)
# CLOUDFLARE_WORKER_URL=https://sandbox.your-domain.workers.dev

# Multi-provider API keys (optional, only for non-Claude models)
# OPENAI_API_KEY=sk-...
# MINIMAX_API_KEY=...
# OPENROUTER_API_KEY=sk-or-... # for Google Gemini via OpenRouter

# Universal Advisor (AI provider for generation, evolution, evaluation)
# SANDCASTLE_ADVISOR_PROVIDER=anthropic  # anthropic|openai|mistral|ollama|omlx|google|minimax (default: anthropic)
# SANDCASTLE_ADVISOR_MODEL=              # override default model for advisor
# MISTRAL_API_KEY=...                    # Mistral AI API key (EU region)
# OMLX_BASE_URL=http://localhost:8080    # oMLX server URL (local inference)
# DATA_RESIDENCY=                        # eu|local|"" (empty = no restriction)

# E2B custom template (pre-built sandbox with SDK installed)
# E2B_TEMPLATE=sandcastle-runner

# Database (empty = SQLite local mode)
DATABASE_URL=

# Redis (empty = in-process queue)
REDIS_URL=

# Storage
STORAGE_BACKEND=local          # "local" or "s3"
DATA_DIR=./data                # SQLite + local storage base path
# STORAGE_BUCKET=sandcastle-data  # S3 only
# STORAGE_ENDPOINT=http://localhost:9000
# AWS_ACCESS_KEY_ID=minioadmin
# AWS_SECRET_ACCESS_KEY=minioadmin

# Security
WEBHOOK_SECRET=your-webhook-signing-secret
AUTH_REQUIRED=false
CREDENTIAL_ENCRYPTION_KEY=     # Fernet key for credential encryption (empty = plaintext)
API_KEY_PEPPER=                # HMAC pepper for API key hashing
KEY_ROTATION_GRACE_HOURS=24    # Grace period for rotated keys
CSP_REPORT_ONLY=false          # Content-Security-Policy in report-only mode

# Budget
DEFAULT_MAX_COST_USD=0    # 0 = no global budget limit
MAX_WORKFLOW_DEPTH=5      # max recursion depth for hierarchical workflows

# Agent Memory
MEMORY_BACKEND=local           # "local" (SQLite + embeddings) or "cloud"
MEMORY_MAX_AGE_DAYS=90         # TTL for memory decay (0 = keep forever)
MEMORY_ADMIT_THRESHOLD=0.3     # Minimum quality score for admission
MEMORY_GRAPH_ENABLED=false     # Enable Neo4j graph backend

# License
LICENSE_KEY=                   # sc_lic_... (community tier if empty)

# EU AI Act / Compliance
COMPLIANCE_MODE=               # "eu_ai_act" to enable enforcement
PRIVACY_MODE=redact            # "redact" | "audit_only" (default: off)
PRIVACY_PATTERNS=email,phone,ssn,credit_card,ip,iban,dob

# OpenTelemetry (requires pip install sandcastle-ai[otel])
OTEL_ENABLED=false
OTEL_ENDPOINT=http://localhost:4318
OTEL_SERVICE_NAME=sandcastle

# Browser step modes
# BROWSERBASE_API_KEY=...
# BROWSERBASE_PROJECT_ID=...
# LIGHTPANDA_PATH=lightpanda    # path to lightpanda binary

# Dashboard
SANDBOX_ROOT=             # restrict browse + CSV export to this directory
DASHBOARD_ORIGIN=http://localhost:5173
WORKFLOWS_DIR=./workflows
LOG_LEVEL=info
```

---

### oMLX - Local AI on Apple Silicon

Run AI workflows entirely on your Mac with oMLX. Zero cloud costs, zero data leaving your machine.

**Setup:**
```bash
# Install oMLX server
pip install omlx

# Start with Llama 4 Scout (17B, fits in 16GB RAM)
omlx serve mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit

# Configure Sandcastle
export SANDCASTLE_ADVISOR_PROVIDER=omlx
export OMLX_BASE_URL=http://localhost:8080

# Run
sandcastle serve
```

**Available models:**

| Model | RAM | Best for |
|-------|-----|----------|
| Llama 4 Scout 17B | 12 GB | General research, analysis |
| Mistral Small 24B | 16 GB | Structured tasks, coding |
| Gemma 3 27B | 18 GB | Creative writing, summaries |
| Qwen 3 30B | 20 GB | Multilingual, reasoning |

**Custom server URL:**
```bash
# Point to remote oMLX server
export OMLX_BASE_URL=http://192.168.1.100:8080
```

---

## Development

```bash
# Run tests (14,600+ passing)
uv run pytest

# Type check backend
uv run mypy src/

# Type check frontend
cd dashboard && npx tsc --noEmit

# Dashboard dev server (starts with demo data when backend is offline)
cd dashboard && npm run dev

# Docker - local mode (SQLite, no PG/Redis needed)
docker compose -f docker-compose.local.yml up

# Docker - full stack (PostgreSQL + Redis + worker)
docker compose up -d
```

---

## Acknowledgements

Sandcastle's architecture was originally inspired by [**Sandstorm**](https://github.com/tomascupr/sandstorm) by [**@tomascupr**](https://github.com/tomascupr) - one of the cleanest abstractions for sandboxed agent execution. While Sandcastle has since evolved its own runtime (Sandshore) with pluggable backends, the original design philosophy of "full system access, completely isolated" remains at the core.

Created by [**Tomas Pflanzer**](https://github.com/gizmax) ([@gizmax](https://github.com/gizmax)).

---

## License

[BSL 1.1](LICENSE)

---

<p align="center">
  <strong>Define in YAML. Run anywhere. Ship to production.</strong>
</p>
