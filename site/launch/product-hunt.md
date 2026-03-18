# Product Hunt Launch - Sandcastle v0.23

## Listing Details

**Name:** Sandcastle
**Tagline:** Stop babysitting your AI agents - EU AI Act compliant orchestrator
**URL:** https://sandcastle-ai.eu
**Category:** Developer Tools / AI / Productivity

## Description (short - 260 chars)

Production-ready AI agent workflow orchestrator. Define pipelines in YAML, run with pluggable sandboxes, monitor via real-time dashboard. EU AI Act compliant with audit trail, PII redaction. 63 integrations. Free and open-source.

## Description (full)

### The Problem

You build an AI agent. It works in a notebook. Then you need it in production: retries, cost tracking, approval gates, audit logs, compliance. You end up writing more orchestration code than agent code.

### The Solution

Sandcastle runs your agent workflows so you don't have to.

**YAML in, results out.** Define your pipeline, pick your models (Claude, GPT, Gemini, Ollama), connect your tools, and go. Zero infrastructure for local mode. Scale when ready.

### What makes it different

- **EU AI Act Ready** - First orchestrator with built-in compliance: risk classification, tamper-evident audit trail (SHA-256 hash chain), transparency reports, PII redaction
- **63 Integrations** - Slack, GitHub, Jira, Salesforce, HubSpot, Stripe, plus Langfuse, Qdrant, GCS, Azure Blob, Exa, and 50+ more
- **5 Sandbox Backends** - E2B, Docker, local process, LightPanda (10x faster browser), Browserbase (cloud)
- **Multi-Provider Routing** - Claude, GPT, Gemini, MiniMax, Ollama in the same workflow
- **Real-time Dashboard** - Runs, costs, approvals, violations, evaluations - all in one place
- **OpenTelemetry** - Workflow and step-level OTLP spans for your observability stack
- **8,700+ Tests** - Battle-tested across 9 deep audit rounds

### Get Started (30 seconds)

```bash
pip install sandcastle-ai
sandcastle init
sandcastle serve
```

## Maker Comment (first comment after launch)

Hey PH! I'm Tomas, and I built Sandcastle because I was tired of writing the same orchestration boilerplate for every AI project.

The v0.23 "Enterprise Trust" release adds something I think the industry needs: built-in EU AI Act compliance. The deadline is August 2, 2026, and most AI tools have zero compliance features. Sandcastle now has risk classification, a tamper-evident audit trail, transparency reports, and PII redaction - all configured in YAML.

Try the live demo (no backend needed): https://gizmax.github.io/Sandcastle/

Would love your feedback. What features would make you switch from your current AI orchestration setup?

## Media

1. **Thumbnail (240x240):** Sandcastle logo
2. **Gallery images (5):**
   - Dashboard overview with health score
   - Workflow builder
   - Compliance page with EU AI Act features
   - Run detail with transparency report
   - Terminal with `pip install sandcastle-ai`
3. **Video (optional):** 60s demo - init -> create workflow -> run -> see results in dashboard

## Topics/Tags

- Artificial Intelligence
- Developer Tools
- Open Source
- Compliance
- Workflow Automation

## Launch Day Checklist

- [ ] Submit listing at https://www.producthunt.com/posts/new
- [ ] Set launch date (Tuesday or Wednesday, 00:01 PST)
- [ ] Prepare 5 gallery screenshots
- [ ] Record 60s demo video (optional but high-impact)
- [ ] Draft 3 replies to common questions (pricing, self-hosting, comparison)
- [ ] Post on Twitter/X: "We just launched on Product Hunt..."
- [ ] Post on LinkedIn
- [ ] Post on Reddit r/artificial, r/MachineLearning, r/SideProject
- [ ] Share in relevant Discord servers (AI, DevTools)
- [ ] Email existing users/contacts

## Social Media Posts

### Twitter/X

Launching Sandcastle on Product Hunt today.

It's a workflow orchestrator for AI agents - YAML in, results out.

What's new in v0.23:
- EU AI Act compliance (first in category)
- Tamper-evident audit trail
- PII redaction
- 63 integrations
- 8,700+ tests

Free & open-source: https://sandcastle-ai.eu

### LinkedIn

Excited to share Sandcastle v0.23 "Enterprise Trust" - a production-ready workflow orchestrator for AI agents.

Why it matters: The EU AI Act deadline is August 2, 2026. Most AI tools have zero compliance features. Sandcastle now includes:

- Risk classification per EU AI Act categories
- Tamper-evident audit trail (SHA-256 hash chain)
- Transparency reports (Article 13)
- PII redaction with 7 pattern types
- 63 integrations, multi-provider model routing
- Real-time dashboard

Get started in 30 seconds: pip install sandcastle-ai

Live demo: https://gizmax.github.io/Sandcastle/
Website: https://sandcastle-ai.eu

### Reddit (r/artificial or r/SideProject)

**Title:** I built an open-source AI workflow orchestrator with built-in EU AI Act compliance

**Body:**
After months of building AI agent pipelines, I got tired of writing the same orchestration code. So I built Sandcastle.

Define workflows in YAML. Pick your models (Claude, GPT, Gemini, Ollama). Connect 63 integrations. Run locally with zero infrastructure, or scale to production.

v0.23 adds "Enterprise Trust" features:
- EU AI Act compliance (risk classification, audit trail, transparency reports)
- PII redaction (7 pattern types, per-workflow config)
- OpenTelemetry instrumentation
- LightPanda & Browserbase browser modes
- 5 new connectors (Langfuse, Qdrant, GCS, Azure Blob, Exa)

8,700+ tests. Battle-tested across 9 deep audit rounds.

Try it: `pip install sandcastle-ai && sandcastle serve`

Live demo (no backend): https://gizmax.github.io/Sandcastle/
GitHub: https://github.com/gizmax/Sandcastle
Website: https://sandcastle-ai.eu

Happy to answer any questions!
