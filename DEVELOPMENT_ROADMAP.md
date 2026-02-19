# Sandcastle - Development Roadmap & Strategic Analysis

**Author:** Tomas Pflanzer @gizmax
**Date:** 2026-02-19

---

## 1. Market Context

- Autonomous AI agent market: **$8.5B by 2026**, projected $35-45B by 2030 (Deloitte)
- **40% of enterprise apps** will have task-specific AI agents by end of 2026, up from <5% in 2025 (Gartner)
- **1,445% surge** in multi-agent system inquiries from Q1 2024 to Q2 2025 (Gartner)
- MCP (Model Context Protocol): **97 million monthly SDK downloads** by late 2025

---

## 2. Competitive Landscape

### Tier 1: Direct Competitors (Code-First Agent Frameworks)

| Framework | Strengths | Weaknesses vs Sandcastle |
|-----------|-----------|--------------------------|
| **LangGraph** (LangChain) | Graph-based workflows, checkpointing, LangSmith observability, massive ecosystem | Heavy abstraction, vendor lock-in, no native sandboxing, requires separate LangSmith subscription |
| **CrewAI** | Intuitive role-based agents, beginner-friendly | Sequential-first, no sandboxing, 2.2x slower than LangGraph |
| **AutoGen** (Microsoft) | Conversational multi-agent, AG-UI support | Awkward for structured DAG workflows |
| **OpenAI Agents SDK** | Minimalist, first-party OpenAI | OpenAI-only, no orchestration/scheduling/persistence |
| **Google ADK** | Deep Gemini integration, A2A protocol | Google ecosystem lock-in |
| **Mastra** | TypeScript-native, $13M YC seed, 150k weekly downloads, memory systems, MCP support | TypeScript-only ecosystem |
| **AWS Strands** | Model-first, Bedrock integration | AWS-centric |

### Tier 2: Low-Code/No-Code

| Platform | Notes |
|----------|-------|
| **Dify** | 130k+ AI apps, visual RAG pipeline builder, self-hostable |
| **n8n** | 230k+ active users, 400+ integrations, general automation |
| **Flowise / Langflow** | Open-source visual LLM workbenches, less production-grade |

### Tier 3: Sandbox Execution Platforms

| Platform | Key Feature |
|----------|-------------|
| **E2B** (current) | Firecracker microVMs, ~150ms startup, open-source, 24h session limit |
| **Daytona** | Docker containers, sub-90ms provisioning |
| **Blaxel** (YC S25) | Perpetual sandboxes, 25ms resume |
| **Sprites.dev** (Fly.io) | Stateful sandboxes, checkpoint/rollback |

---

## 3. Sandcastle's Unique Differentiators

### Strong (Lean Into These)

1. **Zero-Config to Production** - `pip install sandcastle-ai && sandcastle init && sandcastle serve`. No other framework offers this complete experience.
2. **Native Sandboxed Execution** - Built-in E2B integration. No competitor has sandboxed code execution by default.
3. **Single-Binary Architecture** - API + Dashboard + Worker on one port. No Docker, no Redis required for local mode.
4. **YAML-First Workflows** - Declarative, version-controllable, PR-reviewable. Google ADK is moving toward YAML too, validating this approach.
5. **Built-In Cost Tracking** - Native cost tracking per run/step. Competitors require separate paid observability tools.

### Moderate (Good but Not Unique)

- Visual workflow builder, scheduling, webhooks, multi-tenant auth, PDF/CSV export

---

## 4. Feature Gaps - Prioritized Roadmap

### Priority 1 - Critical (Q1 2026)

#### 4.1 Replace Sandstorm with Direct E2B Integration ("Sandshore")
- Remove Sandstorm dependency (~500 LOC Python wrapper)
- Direct `e2b` SDK integration via `AsyncSandbox`
- Bundle `runner.mjs` (Claude Agent SDK) in package
- New module: `src/sandcastle/engine/sandshore.py` with `SandshoreRuntime` class
- Same `query()` / `query_stream()` interface, zero breaking changes for users
- **Impact:** Remove unnecessary dependency, simplify stack, reduce latency

#### 4.2 MCP (Model Context Protocol) Support
- 97M monthly SDK downloads - the standard for tool integration in 2026
- Add `mcp_servers` config to workflow YAML
- Step type `mcp_tool` for calling any MCP-compatible tool
- Enables connection to databases, APIs, file systems without custom code
- **Impact:** Instantly compatible with thousands of MCP servers

#### 4.3 Human-in-the-Loop (HITL) Approval Steps
- `approval_required: true` flag on workflow steps
- Step pause/resume API: `POST /api/runs/{id}/steps/{step_id}/approve`
- Dashboard UI for pending approvals (approve/reject/modify)
- Configurable timeout (auto-approve/reject after N minutes)
- Webhook notification when approval needed
- **Impact:** Unlocks enterprise use cases (finance, healthcare, legal)

### Priority 2 - Important (Q2 2026)

#### 4.4 Multi-Provider Model Routing
- Per-step model selection in YAML: `model: gpt-4o` / `model: claude-sonnet`
- LiteLLM or Portkey integration as optional dependency
- Fallback chains: try Claude, fall back to GPT, fall back to Gemini
- Cost-based routing via existing CostLatencyOptimizer
- **Impact:** Removes "Anthropic-only" perception, 37% of enterprises use 5+ models

#### 4.5 Agent Memory / Context Persistence
- Per-workflow persistent memory store (key-value + vector)
- Short-term (within run) + long-term (across runs) memory
- Cross-run context: "remember what you learned in previous runs"
- Optional Mem0 integration as pluggable backend
- **Impact:** Key differentiator over LangGraph, important for iterative workflows

#### 4.6 Evaluation & Testing Framework
- `sandcastle test` CLI command with golden test cases
- Assertion steps in YAML: `assert: output.sentiment in ['positive', 'neutral']`
- OpenTelemetry export for Braintrust/Langfuse/Datadog integration
- Regression testing: compare current vs baseline output
- **Impact:** Production readiness signal for enterprise

### Priority 3 - Nice-to-Have (Q3 2026)

#### 4.7 A2A Protocol Support
- Expose workflows as A2A-compatible agent endpoints
- Multi-system agent collaboration

#### 4.8 AG-UI Protocol Support
- SSE endpoint compliance with AG-UI event format
- Interoperable with CopilotKit and other AG-UI frontends

#### 4.9 Hybrid Step Types
- `type: agent` (default) - Full E2B sandbox with tools
- `type: llm` - Direct Messages API (cheaper, faster for pure text generation)
- `type: http` - Call REST APIs
- `type: python` - Run arbitrary Python in E2B
- `type: mcp` - Call MCP tools
- `type: condition` - if/else branching
- `type: human` - HITL approval

#### 4.11 Workflow Template Registry
- Public template catalog: curated collection of production-ready workflow templates
- `sandcastle templates list` - browse available templates with descriptions and tags
- `sandcastle templates install <name>` - install a template into the local workflows directory
- YAML-based template format with metadata (author, version, description, required inputs, tags)
- Community contributions via GitHub PRs to a central template repository
- Semantic versioning for templates (install specific version or latest)
- Categories: data-processing, code-generation, research, content-creation, devops

#### 4.10 Additional Features
- Checkpoint/resume for long-running workflows (Temporal-style durable execution)
- Workflow versioning (v1, v2, v3 with diff view)
- Template marketplace (share workflow templates publicly)
- `sandcastle run` CLI for headless execution (CI/CD pipelines)
- Rate limiting per tenant
- Audit log

---

## 5. Sandstorm Replacement Architecture

### New Module: Sandshore (`src/sandcastle/engine/sandshore.py`)

```
Sandcastle Executor
       |
       v
  SandshoreRuntime
       |
       +-- AsyncSandbox.create() (E2B SDK)
       +-- Upload runner.mjs (bundled)
       +-- Run "node runner.mjs" (background)
       +-- on_stdout -> asyncio.Queue -> AsyncIterator[SSEEvent]
       +-- sandbox.kill() (cleanup)
```

**Key design:**
- `SandshoreRuntime.query(request)` - Full result (replaces `SandstormClient.query()`)
- `SandshoreRuntime.query_stream(request)` - Async generator of SSE events (replaces httpx-sse streaming)
- Same external interface, zero changes needed in executor.py beyond import swaps
- `runner.mjs` bundled in package (installed at `sandcastle/engine/runner.mjs`)
- E2B template configurable via env var `E2B_TEMPLATE` (default: "base")

### Migration:

```
BEFORE:                          AFTER:
pip: duvo-sandstorm>=0.4         pip: e2b>=1.4.0
config: SANDSTORM_URL            config: E2B_API_KEY (already exists)
runtime: SandstormClient         runtime: SandshoreRuntime
streaming: httpx-sse over HTTP   streaming: E2B on_stdout + asyncio.Queue
```

---

## 6. Monetization Strategy

### Phase 1: Open-Source Foundation (Current)
- Keep `sandcastle-ai` fully open-source (MIT)
- Build community, GitHub stars, adoption

### Phase 2: Sandcastle Cloud (SaaS)
| Tier | Price | Limits |
|------|-------|--------|
| Free | $0 | 100 runs/month, 1 workflow, local mode only |
| Pro | $29/mo | 5,000 runs/month, unlimited workflows, cloud execution, 30-day retention |
| Team | $99/mo | 50,000 runs/month, multi-tenant, RBAC, 90-day retention |
| Enterprise | Custom | Unlimited, SSO/SAML, SLA, dedicated infra, audit logs |

### Phase 3: Enterprise Features (Open-Core)
SSO/SAML, RBAC, audit logging, compliance export, private cloud/VPC, SLA

### Phase 4: Marketplace
Workflow template marketplace (free + paid), community plugins, revenue share

---

## 7. Technology Decisions

### E2B SDK: Python v2.13.2
- `AsyncSandbox` for async operations
- Streaming via `on_stdout` callback
- File ops: `sandbox.files.write()`, `sandbox.files.read()`
- Lifecycle: `create()`, `kill()`, `set_timeout()`

### Claude Agent SDK
- **TypeScript:** `@anthropic-ai/claude-agent-sdk` v0.2.45 (npm) - used in runner.mjs
- **Python:** `claude-agent-sdk` v0.1.38 (PyPI) - potential future use
- Note: Python SDK still requires Node.js internally (wraps CLI binary)
- Current approach (runner.mjs) remains optimal

### Naming Decision
Runtime module: **Sandshore** (`SandshoreRuntime`)
- Fits "sand" theme
- Represents interface between Sandcastle and cloud execution
- Concise and memorable

---

## 8. Key Market Trends to Monitor

1. **MCP + A2A Standardization** - Both donated to Linux Foundation under Agentic AI Foundation (OpenAI, Google, Microsoft, Anthropic all signed on)
2. **AG-UI Protocol** - Born from CopilotKit + LangGraph + CrewAI. Standardizes agent-frontend communication.
3. **Multi-Agent Systems** - Shift from single agents to orchestrated teams of specialized agents
4. **Human-on-the-Loop** - Moving from "human approves every action" to "human sets guardrails and monitors"
5. **Agent Memory as Infrastructure** - Mem0 achieving 26% improvement over baseline, AWS AgentCore Memory as managed service
6. **LLM Gateways** - Portkey (1,600+ LLMs), LiteLLM (100+ LLMs), OpenRouter (500+ LLMs)
7. **Evaluation & Observability** - Braintrust, Arize, Langfuse becoming standard infrastructure

---

*This document synthesizes research from competitive landscape analysis, E2B SDK documentation, Claude Agent SDK investigation, and market trend reports from Deloitte, Gartner, and industry sources.*
