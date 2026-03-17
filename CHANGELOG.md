# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- CostForecast widget no longer shows inflated projections - inactive days are zero-filled in the 30-day historical series instead of being omitted, fixing daily_average, trend, and projected_monthly calculations.
- Eval regression insight now correctly uses 0..1 scale from backend (raw from DB), not 0..100. Drop threshold >0.1 = >10pp.
- `/advisor/explain` now has `execution_limiter` rate limiting and proper upstream error wrapping (502), matching `/generate` and `/generate/chat` endpoints.
- `/advisor/explain` now scrubs secrets (bearer tokens, API keys, AWS keys, long hex strings) from error and prompt content before sending to external LLM.
- `/runs/estimate` uses proper Pydantic `RunEstimateRequest` schema - malformed JSON now returns 422 instead of 500.
- Cost estimator no longer reports $0.00 for unknown models - falls back to sonnet pricing with a note.
- Composio step validation: `validate()` now rejects composio steps without `composio_config.action`.

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
