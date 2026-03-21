# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.25.1] - 2026-03-21 — "UI Finetuning"

### Changed
- **Bell simplified** - removed Health tab, micro ScoreRing, advisor props. Bell = notifications only. Health info lives in overview.
- **AI Assistant** - merged "AI Generate" and "Edit with AI" into single "AI Assistant" button. Empty builder = generate, existing workflow = edit. Context decides.
- **Health hero** - score ring is now clickable (links to /system-health). Shows "All systems healthy" pill when score >= 80.

### Fixed
- EvolutionPage Recharts formatter type compatibility for CI builds

## [0.25.0] - 2026-03-20 — "Evolution"

### Added
- **Workflow Evolution** - autonomous workflow optimization using autoresearch principles. Set an eval suite, click "Evolve", and Sandcastle autonomously mutates prompts, swaps models, and simplifies steps - keeping only changes that improve your composite score.
- **Composite Scoring** - `quality * confidence - cost_penalty - latency_penalty`. Four optimization modes: quality, cost, latency, balanced. Confidence scales with eval run count.
- **3 Mutation Operators** - prompt refinement (LLM-guided analysis of eval failures), model swapping (haiku/sonnet/opus ladder based on cost/quality), simplification (reduce max_turns, remove tools, prune leaf steps).
- **Evolution Orchestrator** - async loop: load baseline, eval, mutate, eval, keep/discard. Tracks iterations, persists to DB, returns best variant.
- **Evolution API** - `POST /evolution/start`, `GET /evolution/{name}/status`, `POST /evolution/{name}/accept`, `POST /evolution/{name}/cancel`, `GET /evolution/stats`.
- **Evolution Dashboard** - experiment tracking with score evolution chart, iteration table (mutation type, description, score, keep/discard), baseline vs best comparison, Start Evolution modal with workflow selector and eval suite editor.
- **"Evolve" button** on Workflow Detail page for one-click evolution start.
- **DB models** - WorkflowEvolution (experiment tracking) and EvolutionIteration (per-mutation results with lineage).
- 61 new tests for evolution engine.

## [0.24.1] - 2026-03-20 — "The Mother of All Tests"

### Fixed
- **18 bugs fixed** across 2 critical, 4 high, and 12 medium severity findings
- React #310 crash on RunDetailPage (hooks called after conditional returns)
- Environment variable exposure via template system (`{env.API_KEY}` leaked secrets to LLM)
- Template injection via user-controlled workflow inputs
- API route paths doubled as `/api/api/v1/` instead of `/api/v1/`
- Heatmap day-of-week double offset (Monday displayed as Wednesday)
- Emergency stop button posted to wrong endpoint (`/compliance/` vs `/admin/`)
- CSP `unsafe-inline` in script-src negated XSS protection
- CORS hardcoded localhost ports in production
- SSE API key leaked in URL query parameter (replaced with fetch-based streaming)
- Connection strings with credentials visible in DOM
- Missing rate limiting on destructive endpoints (DLQ retry, cancel, approval)
- Email regex catastrophic backtracking on large inputs (10s -> 0.68s)
- Secret scrubber gaps: credential URLs, PEM keys, Azure AccountKey, JSON-quoted secrets
- Eval regression false negatives from IEEE 754 floating-point edge cases
- Missing audit event on workflow publish action
- Race condition on concurrent publish calls (added `with_for_update`)
- Empty `@upload:` file_id leaked raw token to LLM prompt
- Anomaly `run_id` typed as non-nullable but backend emits empty string

### Added
- **1,100+ new tests** — branch coverage increased from 77% to 90%
- Coverage tests for: routes (91), executor + autopilot (283), storage + pdf + eval (215), CLI (170), deep executor steps (128), dense block push (153), final gaps (131+)
- Per-module highlights: autopilot 52% -> 94%, storage 69% -> 90%, CLI 50% -> 75%

### Changed
- Dashboard dist/ removed from git — CI builds in GitHub Actions
- Node.js upgraded from 20 to 22 in GitHub Actions
- Unified navigation across all 5 website pages
- What's New moved to dedicated `/whatsnew/` subpage

## [0.24.0] - 2026-03-19

### Added
- **Workflow as API** - publish workflows as REST endpoints via `POST /workflows/{name}/publish`. Call with `POST /api/v1/{name}`, get OpenAPI spec from `GET /api/v1/{name}/spec`, track usage via `GET /api/v1/{name}/usage`. Supports sync and async execution (Prefer: respond-async header), webhook callbacks, and scoped API keys (allowed_workflows field).
- **Living Dashboard** - real-time sparklines from `GET /stats/sparklines`, 26-week heatmap from `GET /stats/heatmap`, z-score anomaly detection from `GET /stats/anomalies`. SSE auto-refresh on run.completed/failed events. Heatmap drill-down navigates to filtered runs.
- **Agent Marketplace MVP** - `POST /hub/submit` for community template submissions with YAML validation and slug generation. `GET /hub/community` for paginated listings. `POST /hub/templates/{slug}/rate` for 1-5 star ratings. `POST /hub/templates/{slug}/download` for download tracking. "Publish to Hub" modal in dashboard.
- **Multi-agent delegation polish** - dynamic sub-workflow name resolution via template variables (`workflow: "{steps.router.output}"`). Progress events for delegate steps (step.progress with sub_run_id). Detailed error messages with sub-workflow name, sub-run ID, and depth info.
- **File upload for workflow inputs** - `POST /upload` now works in both local and S3 mode. New `type: file` in input_schema with drag-and-drop FileUploadInput component. `@upload:file_id` resolved in executor (text inlined, binary as @file: reference).
- **SDK methods** - `call_api()`, `publish_workflow()`, `get_workflow_api_spec()`, `get_workflow_api_usage()` in both sync and async clients.
- **HubSubmission DB model** for community template storage with status tracking (pending/approved/rejected).

### Fixed
- React error #310 on RunDetailPage - 3 hooks called after conditional returns (Rules of Hooks violation)
- API route paths doubled as /api/api/v1/ instead of /api/v1/ (router mount prefix)
- Heatmap day-of-week double offset (Mon displayed as Wed)
- Anomaly run_id typed as non-nullable but backend can emit empty string
- Emergency stop dashboard button posted to wrong endpoint (/compliance/ vs /admin/)
- Empty @upload: file_id no longer leaks raw token to LLM prompt
- Missing audit event on workflow publish action
- Race condition on concurrent publish calls (added with_for_update)

### Changed
- Dashboard dist/ removed from git tracking - CI builds in GitHub Actions
- Node.js upgraded from 20 to 22 in GitHub Actions (deprecation June 2026)
- Overview page uses only Bento layout (Focus and Classic variants removed)

## [0.23.0] - 2026-03-18

### Added
- **Tamper-evident audit trail** - new `AuditEvent` model with SHA-256 hash chain (`entry_hash` + `prev_hash`). Hooks on 7 executor lifecycle events + 9 admin route actions. Endpoints: `GET /audit`, `GET /runs/{id}/audit`, `GET /audit/verify/{id}`.
- **EU AI Act risk classification** - `risk_level: minimal|limited|high|unacceptable` in workflow YAML, propagated to Run model and API. Unacceptable risk blocked, high risk requires approval in compliance mode.
- **Global emergency stop** - `POST /admin/emergency-stop` cancels all running/queued workflows, sets Redis/in-memory flag checked by executor.
- **Input prompt logging** - `RunStep.input_prompt` now populated with the resolved prompt sent to the LLM.
- **EU AI Act compliance mode** - `compliance_mode: eu_ai_act` in settings enforces high-risk approval requirements.
- **Transparency report** - `GET /runs/{id}/transparency-report` returns Article 13 report with AI models, human oversight, policy violations.
- **Annex IV generator** - `GET /workflows/{name}/annex-iv` generates technical documentation stub for EU AI Act Annex IV.
- **Compliance status** - `GET /compliance/status` returns active compliance features.
- **Privacy Router** - new `PrivacyRouter` class with 7 PII patterns (email, phone, SSN, credit card, IP, IBAN, DOB). Configurable per-workflow and per-server. Modes: `redact` or `audit_only`.
- **LightPanda browser mode** - `mode: "lightpanda"` for 10x faster headless browsing via CDP.
- **Browserbase browser mode** - `mode: "browserbase"` for cloud-hosted browser sessions with zero cold-start.
- **OpenTelemetry instrumentation** - workflow and step-level OTLP spans with cost, duration, token counts. Optional: `pip install sandcastle-ai[otel]`.
- **5 new connectors**: Langfuse (LLM observability), Qdrant (vector DB), GCS (Google Cloud Storage), Azure Blob Storage, Exa (neural web search). Total connectors: 63.
- Pre-baked Playwright + Chromium in Dockerfile.runner (eliminates 60s cold-start).

## [0.22.0] - 2026-03-17

### Added
- **Composio integration** - new `type: composio` step type gives access to 500+ business app actions (Gmail, Slack, GitHub, Salesforce, HubSpot, and more) through a single step. Includes `composio.mjs` connector with execute_action, list_actions, list_apps, and get_action_schema functions.
- **Error Explainer** - new `POST /advisor/explain` endpoint uses AI to explain step failures in plain language and suggest fixes. Includes rate limiting and upstream error wrapping.
- **Cost Forecast** - new `GET /stats/forecast` endpoint returns real historical cost data (30 days, zero-filled) with 7-day moving average projection. Replaces the demo/synthetic data in the dashboard CostForecast widget.
- **Pre-run cost estimation** - new `POST /runs/estimate` endpoint parses workflow YAML and estimates per-step and total cost before execution, based on model pricing and average token usage. Handles classify/gate model overrides and falls back to sonnet pricing for unknown models.
- **Eval quality regression alerts** - dashboard now detects when eval pass rate drops >10pp between runs and surfaces a warning insight with score deduction. Uses real backend field mapping (date, avg_pass_rate, runs).
- **Advisor provider selector** - new `SANDCASTLE_ADVISOR_PROVIDER` (anthropic/openai/ollama) and `SANDCASTLE_ADVISOR_MODEL` env vars allow switching the AI backend for workflow generation, chat, and error explanation. All three generate endpoints now use the unified provider config with settings fallback.
- Mock handler for `/stats/forecast` so dashboard demo mode shows forecast data instead of "No data"

### Changed
- **Retry backoff now uses jitter** - `_backoff_delay()` uses full jitter (`random.uniform(0, base)`) to prevent thundering herd on retries. Exponential: [0, min(2^attempt, 60)], fixed: [1.0, 3.0].
- **Composio implicit dependency inference** - `composio_config.action`, `params` (deep JSON scan), `connected_account_id`, and `app` fields are now scanned for `{steps.X.output}` references, so the DAG planner correctly orders Composio steps after their producers.
- **Deep template resolution for Composio** - `_resolve_params_deep()` recursively resolves template variables in nested dicts and lists inside composio_config.params.
- **Provider-adaptive LLM calls** - `generate_workflow()`, `generate_chat()`, and `explain_error()` now use `_build_request_body()` and `_parse_response_text()` adapters that emit correct request/response format for Anthropic (system top-level, content[0].text) vs OpenAI/Ollama (system role in messages, choices[0].message.content).
- **`_resolve_api_key()` per-provider** - now reads provider-specific key (OPENAI_API_KEY for openai provider) and falls back to settings.openai_api_key before settings.anthropic_api_key.
- `/runs/estimate` now runs full `validate(workflow)` and returns `validation_errors` in response alongside cost estimates.
- Cost estimator: classify and gate steps now estimate as single LLM call (turns=1) matching runtime behavior. sub_workflow parent no longer gets a fake LLM charge. Gate model resolution reads from `strategies[].config.model` matching runtime.

### Fixed
- **Secret scrubber hardened** - `_scrub_secrets()` now catches credential URLs (`postgres://user:pass@host`, `redis://:pass@host`, etc.), PEM private key blocks (RSA, EC, DSA, ENCRYPTED), Azure `AccountKey=`, compound keywords (`aws_secret_access_key=`), and JSON-quoted secrets (`"password": "value"`). Two-layer defense: PEM regex runs first, then token regex. Idempotent (double-scrub produces same output).
- **Eval regression detection IEEE 754-safe** - switched from fragile float comparison (`drop > 0.105`) to integer basis points (`Math.round(drop * 10000) > 1000`). Eliminates false negatives for 10.1-10.5pp drops and false positives for exactly-10pp drops caused by floating-point imprecision.
- **`/runs/estimate` now returns `valid` field** - response includes `valid: true/false` based on `validate()` result. Invalid workflows get "unreliable" disclaimer. Clients no longer need to manually check `validation_errors` array.
- **`useAdvisor` now surfaces API errors** - hook collects errors from all 13 API endpoints into `errors: string[]`. Overview page shows warning banner ("Advisor data incomplete - X endpoints failed") instead of silently displaying empty/default data.
- **DLQ test mocks use sync `session.add()`** - fixed `AsyncMock` for SQLAlchemy async session's synchronous `.add()` method, eliminating unawaited-coroutine warnings. Audited entire test codebase.
- CostForecast widget no longer shows inflated projections - inactive days are zero-filled in the 30-day historical series instead of being omitted, fixing daily_average, trend, and projected_monthly calculations.
- Eval regression insight now correctly uses 0..1 scale from backend (raw from DB), not 0..100.
- `/advisor/explain` now has `execution_limiter` rate limiting and proper upstream error wrapping (502), matching `/generate` and `/generate/chat` endpoints.
- `/runs/estimate` uses proper Pydantic `RunEstimateRequest` schema - malformed JSON now returns 422 instead of 500.
- Cost estimator no longer reports $0.00 for unknown models - falls back to sonnet pricing with a note.
- Composio step validation: `validate()` now rejects composio steps without `composio_config.action`.
- Dashboard eval regression test suites made consistent (removed stale bug-expectation tests).

## [0.21.0] - 2026-03-05

### Added
- DELETE /workflows/{name} API endpoint with active-runs safety check
- Bulk delete workflows UI (multi-select, action bar, confirmation dialog)
- Memory health check displayed at `sandcastle serve` startup
- UGC Creative Studio workflow with Gemini image generation
- Image preview in approval pages and run detail
- @file: lazy loading for large base64 content in HTTP steps

### Changed
- Agent Memory now uses Anthropic Claude Haiku + fastembed local embeddings (no OpenAI key needed)
- Replaced fragile monkey-patch with proper _SandcastleAnthropicLLM subclass
- OPENAI_API_KEY moved to optional multi-model section in `sandcastle init` wizard
- Empty output guard expanded to all LLM/agent workflow steps

### Fixed
- LLM steps with empty output no longer silently break downstream steps
- @file: handler now raises errors on missing files instead of returning empty string
- @file: handler supports quoted paths with spaces

### Dependencies
- Added fastembed>=0.3.6,<0.4 and anthropic>=0.50 to memory optional deps

## [0.20.0] - 2026-02-20

### Added
- Nano-banana AI image generation connector + UGC product shoot pipeline
- Nano-banana integration UI in dashboard integrations

### Fixed
- Deep audit wave 9: 950+ tests and fixes across SDK, A2A, AG-UI, crypto, license, config, routes, webhooks

## [0.18.0] - 2025-12-15

### Added
- Hub security scanner with template scanning, checksums, CI validation
- Update notification banner to dashboard
- Text input mode to Run Workflow modal
- Lighthouse retro 80s startup banner with Knight Rider boot sequence
- Favorites, search autocomplete, recently viewed in hub UI
- Input schema guidance to hub UI
- Helios iNuvio and ABRA Gen ERP integrations
- Offline Ed25519 license key validation system
- Tools/integrations to global search
- Lighthouse context-aware operations advisor

### Fixed
- Shell injection vulnerability
- Broken delegate step
- Stale cache issues
- Workflow audit: step data flow, input validation, output export
- Auto-detect and kill stale process on port conflict in serve
- Hub install 404s and overview empty state
- Use stable ~/.sandcastle/ for data and workflows dirs
- Template YAML preview empty - mock keys naming mismatch
- Community collections empty - slug mismatch + missing hub routes
- Template quality and community card field mismatches
- Token cost tracking in Claude SDK runner

### Changed
- Comprehensive audit: 60+ fixes across engine, API, dashboard, CLI
- Deep audit rounds 2-7: 90+ to 65+ fixes per round
- Comprehensive security: penetration tests and attack vector coverage

## [0.17.0] - 2025-11-10

### Added
- Phase 3 hybrid step types: delegate, transform, notify
- Pricing page (hidden, not linked)
- Security page with 6 hardening enhancements
- 131 template entries synced with registry
- Brain icon for agent steps in workflow builder
- Context-aware operations advisor

### Fixed
- CommunityCard field name mismatch and missing null guards
- Template pack names don't match actual template names
- YAML preview empty - mock TEMPLATE_YAMLS keys naming
- Community collections empty due to slug mismatch
- Executor bugs: cache key, apply_variant, DLQ error handling
- Main search bar showing duplicate on community view
- Broken inline code layout in security page list items
- 6 audit findings: duplicate run guard, stuck QUEUED, query token scope, path traversal, rate limit logging, React ref mutations

### Changed
- Integrations page redesigned with task-oriented UX
- Contact email updated to tom@pflanzer.cz
- Comprehensive README refresh with light mode screenshots
- Deep audit rounds 4-5: 40+ and 40+ fixes

## [0.16.0] - 2025-10-20

### Added
- Landing page redesign

## [0.15.0] - 2025-10-10

### Fixed
- License metadata in pyproject.toml

## [0.11.0] - 2025-08-15

### Added
- Initial release version

## [0.10.0] - 2025-08-10

## [0.9.0] - 2025-08-05

### Fixed
- Demo auth gate

## [0.8.0] - 2025-08-01

### Fixed
- Missing auth files and js-yaml types for CI

## [0.7.1] - 2025-07-28

## [0.7.0] - 2025-07-25

## [0.6.0] - 2025-07-20

### Fixed
- CI dashboard build

### Added
- Social share OG image and Twitter Card meta tags

## [0.5.0] - 2025-07-15

## [0.3.1] - 2025-07-01

## [0.3.0] - 2025-06-25

### Changed
- README rewrite with Policy Engine and Optimizer focus
- Removed version-specific sections

## [0.2.0] - 2025-06-20

### Fixed
- Path traversal vulnerability
- Budget precedence and post-stage checks
