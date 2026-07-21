# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.42.2] - 2026-07-21 - "Workflows That Stay"

### Fixed
- Docker: `WORKFLOWS_DIR` is now a shared, persistent volume (`app_workflows`)
  on the `sandcastle`, `scheduler`, and `worker` services. User-created and
  generated workflows previously lived in each container's own filesystem, so
  they silently vanished on every rebuild - after which "Replay from Step"
  failed with `WORKFLOW_NOT_FOUND` - and the API, worker, and scheduler could
  each see a different copy. Existing deployments: workflows created before
  this fix were stored inside the old containers and cannot be recovered;
  re-save or re-generate them once, after which they persist.

## [0.42.1] - 2026-07-21 - "Assistant, Actually"

### Fixed
- The Run Assistant is now a real assistant. The dashboard sidebar previously
  answered from client-side English keyword patterns with no model behind it,
  so non-English questions (and any question outside the patterns) got canned
  replies. New `POST /api/runs/{run_id}/assistant` serializes the run
  (status, steps, errors, output tails - secret-scrubbed, tenant-scoped) and
  answers via the advisor LLM, which on a local-first box means your own
  model. Answers follow the language of the question. The old heuristics
  remain only as an offline fallback when no provider is configured
  (the endpoint says NO_PROVIDER explicitly).

## [0.42.0] - 2026-07-21 - "Your Box, Your Default"

### Added
- **Default model picker.** New `workflow_default_model` setting: steps without an
  explicit `model:` use it instead of the built-in default. Settable from the
  onboarding wizard - detected local providers now list their models (Ollama tags,
  vLLM/NIM served ids) and one click makes one the default - or via
  `PATCH /api/settings {"workflow_default_model": "nim/ornith"}`. Values are
  validated against the model registry at write time; unknown strings are rejected
  with `UNKNOWN_MODEL` before they can break a run. Empty string clears it.
- **Dynamic `ollama/<tag>` models.** Any locally pulled Ollama model resolves like
  `nim/<id>` always has (`ollama/qwen3:8b`, region `local`, $0), with the same
  character discipline against injection. The base URL honours `OLLAMA_HOST`.
- `/health/providers` includes a `models` list for reachable local providers
  (up to 20 each), which is what the wizard picker renders.

### Fixed
- The workflow generator ("describe it in plain English") no longer demands a
  cloud API key on a box that runs local models. Advisor provider resolution
  picks, in order: `SANDCASTLE_ADVISOR_PROVIDER`, any cloud provider with a
  configured key, the local provider named by `workflow_default_model`, and on
  a Spark a reachable local NIM/vLLM. Local generation calls use the model you
  picked (e.g. `ornith`), not a hardcoded catalogue name.

### Changed
- A step whose model is the bare default now resolves: `workflow_default_model`
  (when set) -> Spark NIM autoroute (when applicable) -> `sonnet`. Explicit
  non-default models are never rewritten. Note that `model: sonnet` written
  explicitly counts as the bare default - the same convention the Spark autoroute
  has used since 0.33.
- The onboarding wizard keeps the cloud API key entry visible when local
  providers are detected (was either/or), so you can add Anthropic, Mistral, or
  OpenAI keys alongside a local model.

## [0.41.2] - 2026-07-21 - "Local Discovery"

### Fixed
- Provider discovery now respects `OLLAMA_HOST` everywhere. The onboarding wizard,
  `/health/providers`, `/advisor/status`, and the EU-residency check probed a hardcoded
  `http://localhost:11434`, so a Docker deployment pointing at a host Ollama (e.g.
  `host.docker.internal`) always showed "No provider detected" even though workflow
  steps could reach it. Ollama LLM steps and the advisor test call resolve their base
  URL the same way now.
- The wizard and `/advisor/status` also discover a local NIM / vLLM server: any
  OpenAI-compatible endpoint at `NIM_BASE_URL` whose `/v1/models` answers shows up as
  a detected local provider, so a box running vLLM no longer looks empty.

### Added
- `SPARK_NIM_DEFAULT_MODEL`: the model the Spark NIM autoroute sends bare default
  steps to (default `nim/llama-3.1-70b`). Set it to a model your local server
  actually serves (e.g. `nim/ornith`) - the reachability probe only checks that
  `/v1/models` answers, not that the hardcoded default exists on it.

## [0.41.1] - 2026-07-21 - "Spark, Contained"

### Fixed

- Docker: new `docker-compose.gpu.yml` override passes the NVIDIA GPU through to the
  `sandcastle` and `worker` containers. Without it, Spark Mode detection (which is
  fail-closed and probes `nvidia-smi` in-process) silently reported `is_spark=False`
  in the official compose deployment, even on a real DGX Spark. Verified on GB10
  hardware: with the override, containers detect Spark Mode and auto-lift worker
  concurrency. Requires nvidia-container-toolkit on the host.
- Docker: the `worker` service now uses a process-based healthcheck. It inherited the
  image's HTTP healthcheck but runs the arq queue worker with no HTTP server, so it
  was reported permanently unhealthy and `docker compose up --wait` could never
  succeed.

## [0.41.0] - 2026-07-21 - "Trust, Verified"

A full adversarial audit of the engine, API, persistence, dashboard, and deployment, followed by
a hardening pass on everything it found. 17,000+ tests, three consecutive randomized-order soak
runs, zero flakes.

### ⚠️ Upgrade notes (read before upgrading)

- **Database: plan a short maintenance window.** Migrations 015–017 create the missing tables
  and columns, convert legacy lowercase PostgreSQL enum labels to the ORM's member names
  (`running` → `RUNNING`, …), convert JSON columns to JSONB, and add indexes. These take strong
  table locks on `runs`, `run_steps`, `approval_requests`, and `autopilot_experiments` and are
  **not** rolling-deploy safe. Quiesce API/worker/scheduler, back up, then run
  `sandcastle db migrate` (or let the Docker CMD do it) before starting 0.41.0 everywhere.
  Sandcastle < 0.41 cannot read the migrated enum labels. If you ever wrote to
  `workflow_versions` with direct SQL, verify `SELECT DISTINCT status FROM workflow_versions`
  only contains draft/staging/production/archived values — the migration preflights this and
  stops with a clear error listing the offending values.
- **`sandcastle serve` now binds `127.0.0.1` by default**, and binding a non-loopback host with
  `AUTH_REQUIRED=false` is refused (exit 2) unless you set `SANDCASTLE_ALLOW_INSECURE_BIND=true`.
  Docker/compose deployments pass the host explicitly and are unaffected.
- **HTTP step `@file:` references now require an admin-trusted run** and stay confined to
  `DATA_DIR`. Non-admin workflows using `@file:` will fail the step.
- **Published MCP workflow tools now call the authenticated API** instead of executing in
  process; the MCP server needs a reachable Sandcastle API and a valid key. The MCP manifest is
  stdio-only (the never-served `streamable-http` claim and HTTP discovery route are gone).
- **`retry:` is now honored by every step type** (`http`, `code`, `llm`, …), including
  `on_failure: fallback` — workflows that always had retry configured will now actually retry,
  which means more attempts (and cost) than before. Deterministic failures (4xx, validation,
  trust, SSRF, residency) are no longer retried at all.
- **Docker image defaults to a single Uvicorn worker** (`UVICORN_WORKERS:-1`), because every
  worker runs the full application lifespan. Scale out with more containers, or set
  `UVICORN_WORKERS` deliberately with `SCHEDULER_ENABLED=false`.
- **Agent webhooks without `ANTHROPIC_WEBHOOK_SECRET` are rejected (403)** whenever
  `AUTH_REQUIRED=true`; set the secret or disable auth for local development.
- **A2A `tasks/send` returns `submitted` immediately** and enqueues the run; clients must poll
  `tasks/get` instead of expecting inline completion. `tasks/cancel` now signals the worker
  instead of forcing the DB status.
- **Tool connectors receive a minimal environment** (PATH/HOME/locale/temp) plus only their own
  registered `TOOL_*` credentials — connectors relying on inherited provider keys or other
  variables must declare them.
- **Code-step subprocess failures fail closed** by default; set
  `SANDCASTLE_CODE_STEPS_ALLOW_INPROCESS_FALLBACK=true` to restore the legacy in-process
  fallback.
- **Run SSE streams end after 600s** with an error event; long-lived clients should reconnect.
- **Runner/E2B images run as the base image's `node` user** (still uid/gid 1000); scripts
  referencing the `runner` username must use `node`.

### Security
- Closed an authentication bypass on `/admin/environments/*` (root-mounted router skipped the
  auth middleware and `is_admin()` returned true for any caller), with real-middleware
  regression tests; `is_admin()` now requires a successfully authenticated API key.
- `@file:` confinement (resolve + `is_relative_to(data_dir)`, off-loop reads) and the
  admin-trusted gate above; the HTTP step no longer sends an env var *name* as the Bearer token
  when the variable is unset.
- Webhook delivery resolves once, validates, and pins the TCP connection to that exact IP (TLS
  SNI preserved) — the DNS-rebinding TOCTOU is closed on the webhook path too.
- Tool connectors no longer inherit the full process environment (DB URLs, provider keys, other
  tools' credentials); code-subprocess infrastructure failures fail closed by default.
- Self-update uses `sys.executable` instead of PATH-resolved `pip`/`python`, verifies the
  installed version strictly, and stores `.env` backups under `data_dir/backups/` with `0600`.
- `context_source: custom` shell execution, code steps, and `@file:` all sit behind the same
  `admin_trusted` flag, persisted on the run row so restarts/resumes keep it.

### Fixed
- **PostgreSQL production installs**: 10 unmigrated tables + missing columns (migration 015),
  enum labels reconciled with the ORM (016), JSON→JSONB + index alignment (017); `alembic check`
  with `compare_type`/`compare_server_default` and a live enum round-trip smoke test now run in
  CI.
- Engine cost accounting: LLM cost survives post-query failures and aggregates across retries;
  `WorkflowPaused` carries accrued cost and completed siblings are checkpointed, so approval
  resumes no longer double-pay. Sub-workflow/delegate fan-out splits the budget across children.
- API robustness: idempotency race returns the existing run instead of 5xx; batch polling honors
  all terminal statuses; uploads stream in bounded chunks; background tasks keep strong refs;
  run/result persistence is JSON-coerced (`datetime`, `UUID`, custom objects can't lose a run).
- Queue/scheduler: startup cleanup re-enqueues Redis-queued runs with their versioned YAML and
  trust flag; approval-timeout retries with a bounded sweep; audit hash chain serializes appends
  per scope and failed audit writes roll back via SAVEPOINT; local-mode jobs get the same
  timeout arq enforces; `list_schedules` tolerates pending jobs.
- Dashboard: Evolution page matches the real API (tenant filters everywhere, latest-wins
  status); admin SSE uses authenticated fetch; WorkflowBuilder YAML is escaped via js-yaml;
  SSE chunk-boundary parsing fixed; mock fixtures lazy-loaded out of the production bundle; CSV
  formula-injection guard.
- Docker: runner/E2B image builds (uid collision with the `node` user), compose scheduler port,
  `DATA_DIR`/`WORKFLOWS_DIR` container defaults, cookbook blockers; CI now builds all three
  images and boots the API against `/api/health`.

### Added
- `llm_config.temperature` / `max_tokens` per-step overrides (parsed, validated, sent to both
  provider families).
- `GET /api/evolution` (tenant-scoped, admin-gated) and real frontend↔backend contract tests
  (`tests/test_dashboard_api_contract.py`, `tests/test_workflows_valid.py` — every shipped and
  community workflow must parse+validate).
- `sandcastle memory-mcp serve` and `sandcastle tools validate` (documented but previously
  unwired).
- Hub: canonical `hub/registry.json` (283 templates) with the site registry generated from it;
  seeded community gallery restored with 35 real workflows; CI validates both registries
  (schema, sha256, dead links).
- Test infrastructure: randomized-order soak hardening (per-test `os.environ`, settings
  singleton, and scheduler job isolation) — three consecutive 17k-test clean runs.

## [0.40.4] - 2026-07-18 - "Fresh Install Fixed"

### Fixed
- **PostgreSQL migrations failed on fresh installs** - the docker compose `migrate` service exited
  with `DuplicateObjectError: type "runstatus" already exists`, so a new deployment never started.
  Three latent bugs in the migration chain (enum double-create in 001, `create_type` silently
  ignored on generic `sa.Enum` in 005/006, duplicate `ix_runs_parent_run_id` index in 007) are
  fixed, and 007's downgrade now restores the index 003 expects. Existing databases are
  unaffected (already-applied revisions never re-run).
- New `postgres-migrations` CI job runs `python -m sandcastle db migrate` twice against a real
  `postgres:16` service on every PR touching migrations, Docker, or compose files, so migration
  regressions can no longer ship undetected (the test suite runs on SQLite).

## [0.40.3] - 2026-07-11 - "Deeper Isolation"

Follow-up hardening that finishes the three deferred items from the 0.40.2 audit sweep. Defaults
are secure; existing behavior is preserved with per-feature opt-outs.

### Security
- **Code steps now run out-of-process by default.** Validated `code` steps execute in a separate
  Python subprocess with a secret-free environment and POSIX CPU/address-space limits, so a
  sandbox escape cannot reach the parent process memory (`settings`, DB session factory, other
  tenants' data). The subprocess is really killed on timeout (the in-process thread path could
  not be). Set `CODE_STEPS_OUT_OF_PROCESS=false` to use the legacy in-process path; infrastructure
  failures fall back to it automatically so a step is never lost.
- **HTTP steps pin the validated IP at connect time.** The SSRF pre-flight resolves the hostname
  once, validates the address, and a custom transport dials that exact IP while preserving the
  `Host` header and TLS SNI, closing the DNS-rebind TOCTOU. Non-resolvable/mocked hosts are
  unaffected.
- **Memory MCP per-tenant scope enforcement.** With `MEMORY_MCP_SCOPE_PREFIX` set, every tool call
  (`add`/`search`/`list_memories`/`forget`) must stay within the configured scope, so an
  authenticated caller cannot reach another tenant's memories by supplying a different scope
  string. `forget` requires and validates the owning `user_id` against the prefix. Unset preserves
  today's single-tenant behavior.

## [0.40.2] - 2026-07-11 - "Hardened Steps"

Security and correctness patch from a full audit sweep (engine, API, CLI, sandbox, deps). No new
features; existing behavior is preserved except where it was unsafe or wrong.

### Security
- In-process `code` steps are now gated to admin callers by default. The main run endpoints no
  longer pass `admin_trusted=True` unconditionally; a normal tenant can no longer execute
  in-process code steps unless the caller is admin or the operator opts in via
  `CODE_STEPS_ALLOW_UNTRUSTED=true`.
- Hardened the in-process code-step sandbox against blocklist bypass: the file helpers moved to a
  dedicated module whose namespace holds no secrets (closes the `str.format`/`__globals__`
  traversal to `settings`), and an AST validation pass rejects `import`, dunder-attribute access,
  and format strings that traverse a dunder attribute (ordinary `str.format` such as
  `"{}|{:.2f}".format(a, b)` keeps working). The out-of-process sandbox backend remains the fully
  robust isolation.
- Fixed a path-traversal write in the code-step `save_file_b64` helper: the destination is now
  resolved and confined to the data `tmp` directory.
- The Memory MCP server now fails closed over `streamable-http`: it requires `MEMORY_MCP_TOKEN`
  (enforced by a constant-time bearer check) and refuses to start unauthenticated outside local
  mode. The stdio transport is unchanged.
- HTTP steps block redirects and reuse the webhook dispatcher's blocked-network ranges for the
  SSRF pre-flight check.
- Raised dependency floors past known CVEs: `jinja2>=3.1.6` (report extra) and
  `python-multipart>=0.0.18`.

### Fixed
- HTTP steps now fail on 4xx/5xx responses by default (set `fail_on_error: false` to keep the
  legacy pass-through), so `retry.on_failure` triggers and downstream steps no longer consume an
  error body as valid data. The response `status_code` is now exposed on JSON dict outputs.
- Loop steps enforce the run budget mid-loop instead of only after every iteration completes,
  preventing large overshoots of `max_cost_usd`.
- Fan-out (`parallel_over`) steps now persist an aggregate run-step record and set
  `step_results`, so their status is queryable and downstream `steps.X.status` references resolve.
- Race steps can now select a branch whose winning output is legitimately `None` instead of
  reporting "all branches failed".
- E2B custom (prebaked) templates no longer drop workflow tool-connector files; `tool_files` are
  uploaded regardless of whether a template is set.
- `sandcastle providers` probes Ollama at `OLLAMA_HOST` instead of a hardcoded
  `localhost:11434`, so Docker/Spark users see accurate status.
- `sandcastle run <missing>.yaml` reports a clear "file not found" error instead of sending the
  filename to the server and surfacing a generic 404.
- Removed a misleading code-step cancellation comment and the dead cancellation event; the
  in-process timeout behavior is now documented accurately.
- Fixed `.env.example`: replaced the dead `SANDSTORM_URL` (wrong port) with `SANDCASTLE_URL`
  (`http://localhost:8080`) and moved `E2B_API_KEY` into a backend-conditional section.

## [0.40.1] - 2026-07-11 - "Docker on Your Box"

Patch release for teams installing Sandcastle through Docker Compose and for NVIDIA DGX Spark
deployments that run Sandcastle, local model servers, and Docker sandboxes on the same host.

### Added
- Added `docker-compose.spark.yml`, a full-stack Spark override that enables Docker sandboxes,
  host-local NIM/Ollama/vLLM access, Docker socket group wiring, and a prebuilt
  `sandcastle-runner:latest` runner image.
- Added explicit Spark/Docker environment examples to `.env.example` and README, including
  `SPARK_SANDBOX_BACKEND`, `DOCKER_GID`, `NIM_BASE_URL`, and `OLLAMA_HOST`.
- Added an infra-only Compose file (`docker-compose.infra.yml`) for PostgreSQL, Redis, and
  MinIO-only development setups.

### Changed
- The production Docker image now builds the dashboard before packaging, includes `README.md`,
  and installs Sandcastle with the `docker` extra so `aiodocker` is present for the Docker
  sandbox backend.
- Docker Compose startup now runs migrations before the API, scheduler, and worker services.
- Docker runner images now pin Playwright, Claude Agent SDK, and OpenAI npm package versions for
  reproducible builds; the self-hosted Docker cookbook now defaults to Playwright 1.61.1.
- Ruff's target version now matches the package's Python 3.12+ runtime support.

### Fixed
- Fixed Docker builds failing because the package wheel expected `README.md` and
  `dashboard/dist` to exist inside the image build context.
- Fixed authenticated Docker Compose deployments starting without required `API_KEY_PEPPER` and
  `ADMIN_API_KEY` values.
- Fixed Docker-backed Spark deployments where sandbox runner containers could not reach
  host-local model servers by adding `host.docker.internal:host-gateway` wiring.
- Fixed the `ocr` extra by using the published `chandra-ocr` version range, and avoided a yanked
  `grpcio` release in the optional memory dependency path.
- Fixed LightPanda cookbook downloads on ARM64 hosts by selecting the architecture-specific
  Linux binary.
- Fixed self-hosted sandbox cookbooks that tried to install the `ant` CLI from a non-existent
  npm package; they now use the official Anthropic CLI release binary pinned to `v1.17.0`.
- Fixed cookbook Docker build blockers caused by an invalid optional `COPY requirements.txt`
  pattern and a Daytona `COPY skills/` instruction pointing at a directory that is not shipped.
## [0.40.0] - 2026-06-11 - "Build Once, Run Anywhere"

Describe what you want in plain English and Sandcastle builds the workflow. Run it on any model — Claude, GPT, Mistral, a local model on your own box — and move it between them with one line. Deploy it your way: cloud, your server, fully air-gapped, EU-only. And it gets better over time, on its own. Build any agent once; run it on any model, anywhere; watch it improve.

This release rebuilds the experience around that loop and adds the machinery behind it.

### Added
- **Omnibox** — "What should your agent do?" The Overview now leads with one input: describe a task in plain English, Sandcastle generates the workflow, you run it. Also reachable from ⌘K.
- **3-verb navigation + density tiers** — the sidebar collapses to **Build / Run / Improve** (plus a quiet Operate) with a 3-tier density control (**Essentials / Standard / Everything**) replacing the old binary Lite/Full toggle. Essentials is the old Lite; legacy settings migrate automatically.
- **Tabbed Settings hub** — keys, providers, integrations, and advanced config unified under `/settings` (old `/api-keys`, `/integrations`, `/providers` redirect in).
- **Self-explanatory Workflow Builder** — every step type has a hover card (what it does, when to use it, an example, cost note), config fields have inline help, a plain-English summary of the step, and live validation on the canvas.
- **Black Box** — a tamper-evident, signed hash-chain over every recorded run, plus a `black_box` compliance mode and `sandcastle audit verify`. A replayable, verifiable audit trail for the EU AI Act era.
- **The Architect** — generate → run → judge → refine, until the workflow actually works, then ship it with a recorded cassette as a Proven template.
- **Self-Healing Workflows** — dead-letter failures get an LLM diagnosis, a patched workflow version behind an approval gate, and regression tracking. Off by default.
- **Model Time Machine** — replay your real recorded workload against a different model and get a quality / cost / latency delta. Dry-run pricing is free; live replay requires an explicit budget.
- **Sandcastle Mesh** — register multiple machines and route steps by capability (`requires: [gpu, browser]`). A "Fleet" view shows node health. Off by default.
- **Night Shift** dashboard — what your agent learned overnight: adapter lineage, nightly eval deltas, served-adapter view.
- **Mission Control** — a full-screen live run theater at `/runs/:id/live`: the DAG lights up as it executes, with live cost and a thought stream.
- **Real GPU LoRA trainer** for Overnight Self-Tune (`[training]` extra; GPU-gated, dormant without one).
- **Verified template bundles (`.sctpl`)**: every shared template can carry a replayable proof it works. `sandcastle pack` zips a workflow with its recorded cassettes and a SHA-256-checksummed manifest; `sandcastle template verify` replays the proof locally in strict mode — offline, $0 — and reports PASS/FAIL per cassette. `sandcastle template install` verifies before installing (https sources support `--sha256` pinning); `sandcastle template search` queries a static index.
- **`build` / `new` CLI aliases** for `generate`, with the description as a positional argument (`sandcastle build "summarize my tickets"`), mirroring the omnibox.
- **`SECURITY.md`** with a coordinated-disclosure policy, an antivirus false-positive explanation, and download-verification steps; a signed-release workflow that publishes `SHA256SUMS.txt` + cosign keyless signatures, and `scripts/verify-release.sh`.

### Changed
- **New visual identity** — a warm "Sand & Ink" palette, instrument-panel layout with hairline rules, control-room status lights replacing pill badges, illustrated empty states, and a settle-based motion system.
- **New marketing site** — an editorial "Manifesto" homepage and redesigned pricing, security, EU AI Act, Community Hub, and What's New pages.
- README now leads with sovereignty and local-first positioning.
- **LLM/standard steps pin a low sampling temperature** (`step_temperature`, default 0.2, per-step override). Some OpenAI-compatible endpoints default to 1.0 and produce garbled output; this makes step output deterministic across providers.

### Fixed
- `POST /generate` no longer hard-requires `ANTHROPIC_API_KEY` — it accepts any configured advisor provider (Mistral, OpenAI, Ollama, local…) and returns a clear `NO_PROVIDER` 400 only when none is usable. The omnibox proactively warns and disables generation when no provider is connected.
- An empty model response is now a **failed** step with a clear message, instead of a silent "completed" run with no output.
- Code steps that use blocked patterns get an actionable error explaining the sandbox's constraints, and the generator is told those constraints so it emits runnable code.

### Security
- Centralized the test suite's attack-string fixtures into a single runtime-assembled module, removing the exact literal signatures that triggered heuristic antivirus false positives — with zero loss of test coverage.

## [0.33.0] - 2026-06-04 - "Your Box, Your Brains"

Every agent you've ever run phoned home. Sent your data somewhere. Billed you by the token. You accepted it, because that was the deal. The deal just changed.

Plug Sandcastle into an NVIDIA DGX Spark - Grace-Blackwell, 128GB of unified memory - and it knows. No flag, no config. **Spark Mode** lights up, defaults flip to local-first, and a startup banner, a dashboard badge, and `GET /api/runtime` all say the same thing out loud: you're running on your own silicon now. Inference goes first-class local through a `nim/*` provider (NVIDIA NIM, OpenAI-compatible), alongside ollama and oMLX - every one at `region=local`, `$0.00/run`, data that never leaves the box. Then it learns: **Overnight Self-Tune** trains a task-specific LoRA adapter on your workflow's *own* eval data and routes to it. Your box getting better at *your* work, on *your* data, while you sleep - for nothing. $0. Private. It learns overnight.

You wanted to stop renting. Here's the deed. Your agents. Your silicon. Learning your work, costing nothing, never leaving the room.

### Added
- **Spark Mode**: auto-detection of NVIDIA DGX Spark (Grace-Blackwell, 128GB unified memory) that enables local-first defaults with no flags. Exposed via startup banner, dashboard badge, and `GET /api/runtime`.
- **`nim/*` provider**: first-class local inference via NVIDIA NIM (OpenAI-compatible), joining `ollama` and `oMLX`. All run at `region=local`, `$0.00/run`, data-stays-on-box. On a detected Spark, the default model auto-routes to the local NIM.
- **Overnight Self-Tune**: the evolution loop can train a task-specific LoRA adapter on a workflow's own eval data and route to it - local, on your own data, for $0.
- **Deterministic cassettes**: record a run once and replay it offline at $0, identically, with a tamper-evident signature. New **strict mode** hard-fails on a modified cassette.
- **Shareable run permalinks**: public, secrets-scrubbed links with a 30-day TTL.
- **`sandcastle run --local`**: in-process run mode - no server, zero external dependencies, with first-run engine-pick onboarding.
- **Template Hub explosion**: new categories (Automation & RPA and several more), dozens of provider-neutral browser / computer-use RPA workflows across multiple waves, a living **community gallery** (31 workflows from 19 authors, synced to the web hub), and 3 viral creative use-cases (Action Figure Me, Glow-Up Restore, Ad Creative Factory).
- **`tool` step type + UGC Studio**: first-class tool step and a self-healing UGC Studio flow with a vision judge (reference-image creative that checks its own output and retries).
- **`llms.txt` + `llms-full.txt`**: machine-readable surface (repo + site) so code-generating assistants can scaffold a correct workflow first try.
- `sandbox_exec` implementation for browser dom / computer-use / lightpanda modes (previously referenced but never defined).

### Changed
- Local-first defaults activate automatically under Spark Mode (no configuration required).
- Provider-neutral, dark-first dashboard refresh; demo runs seeded with real provider models.
- 2026-06 model refresh: GPT Image 2, Gemini 3.5, GPT-5.2 (retired models removed).
- Share tokens now carry a TTL (30-day default).

### Fixed
- **Sync run endpoint** no longer reports `"completed"` when persistence fails (returns `verification_pending` after retries).
- Browser dom / computer-use / lightpanda modes no longer crash on a never-defined `sandbox_exec` (now implemented; E2B/Docker raise a clear error pending a persistent-sandbox exec path).
- Dashboard recovers from stale-deploy chunk misses instead of a dead-end "Connection error".
- Packaging: restored missing runtime dependencies and editable-install support.
- Test isolation: eliminated the last file-backed-DB flakes.

### Security
- Browser-step SSRF guard (blocks requests to internal/disallowed hosts), share-token TTLs, and NIM model-id validation harden the request and sharing surfaces.

## [0.32.2] - 2026-05-19 - "Your Sandbox, Your Silicon"

Anthropic shipped a managed-agents update on May 19. Most teams will read the blog post and move on. We extracted everything. Self-hosted sandboxes that keep your org key inside your boundary. A Memory MCP server that fills the gap Anthropic left when they declared memory_stores incompatible with self-hosted runs. MCP tunnels with WIF token exchange so your private Jira can be reached by a managed agent without static secrets. Live work-queue telemetry in the dashboard. Three production case-study blueprints. Five sandbox cookbooks. 150 new tests, validated across six iterations. Zero regressions.

You wanted control over where your model runs. Here it is.

### Added - Self-hosted sandboxes (the four blog partners + Docker reference)

- **SelfHostedSandboxConfig** (`sandcastle.engine.self_hosted_sandbox`). Five providers in the enum: `cloudflare`, `daytona`, `modal`, `vercel`, `docker`. Each gets a cookbook under `deploy/cookbooks/`.
- **Org-key leak guard** (`OrgKeyLeakError`). The worker refuses to start if `ANTHROPIC_API_KEY` is present in the sandbox environment. Only `sk-ant-oat01-` environment-scoped keys make it past the gate. A foot-gun that would have leaked your org budget now raises before the first network call.
- **MemoryStoresIncompatibleError**. Anthropic explicitly documented that memory_stores cannot be combined with self-hosted sandboxes. The validator catches the combination at session-payload assembly time instead of letting the API 400 you in production.
- **AWS region warning**. If `ANTHROPIC_REGION=aws-*` or `AWS_REGION=us-*` leaks into worker env, the runtime logs a warning. AWS is not a supported sandbox host for managed-agents per the May 19 spec.
- **SelfHostedWorker** (`sandcastle.engine.self_hosted_worker`). Async worker that polls `/v1/environments/{id}/work`, claims one work item at a time, reclaims abandoned work after 2 s, executes the default 6-tool toolset (`bash`, `read`, `write`, `edit`, `glob`, `grep`) matching `beta_agent_toolset_20260401`.
- **SelfHostedSandboxRuntime** registered in `sandcastle.engine.agent_runtime`. `RUNTIMES = {"auto", "anthropic", "local", "agent-sdk", "self-hosted-sandbox"}`.

### Added - Sandbox cookbooks (`deploy/cookbooks/`)

- **`docker/`** - canonical reference. Two-stage Dockerfile, USER 10001, tini entrypoint, `--rm --network=session-net --tmpfs /workspace --read-only --cap-drop=ALL`, exit codes 64/65/124 mapped to Anthropic's reclaim semantics.
- **`cloudflare/`** - Containers via Workers, environment binding for the env key.
- **`cf-worker/`** - Durable Object + RAM FakeFS, sub-100 ms cold start.
- **`daytona/`** - snapshot-based stateful sandboxes (good for long-running research).
- **`modal/`** - GPU sandboxes (mount `gpu="A10G"` in the Python config).
- **`vercel/`** - VPC + credential brokering, env key NEVER enters the sandbox (broker injects per-call).

### Added - Memory MCP server (the gap Anthropic left)

- **`sandcastle.engine.memory_mcp_server`** - production-grade MCP server wrapping mem0 + persistent Qdrant + Anthropic Haiku for memory decisions. Four tools: `add(text, user_id, metadata)`, `search(query, user_id, limit)`, `forget(memory_id)`, `list_memories(user_id, limit)`. Two resources: `sandcastle://memory/users`, `sandcastle://memory/health`. One prompt: `memory_qa`. CLI entrypoint via `python -m sandcastle.engine.memory_mcp_server`.
- **`MemoryMCPError`** with `code='memory_unavailable'` if mem0 missing - lazy import, typed install hint.
- **Helm chart** at `deploy/mcp-tunnel/memory-mcp/`. Chart.yaml v2, appVersion 0.32.2. 8 templates including deployment with cloudflared sidecar (same pod), Qdrant StatefulSet with 10 Gi PVC, NetworkPolicy egress to Cloudflare CIDR `198.41.192.0/19` + `2606:4700:a0::/44` on TCP+UDP 7844. ServiceAccount, ConfigMap, Secret stub, _helpers.tpl.
- **docker-compose.yml** for local development - 3 services with healthchecks.

### Added - MCP tunnels prep (gated beta, header `mcp-client-2025-11-20`)

- **WIFTokenExchangeClient** (`sandcastle.engine.mcp_tunnel_wif`). OIDC token exchange against a configured issuer, 60 s skew-cached. `assemble_cloudflared_env(config)` produces the env block for WIF or manual-cert modes.
- **MCPTunnelConfig** with `TunnelAuthMode.WIF` and `TunnelAuthMode.MANUAL`. Validator rejects empty tunnel_id, missing servers, duplicate server names, non-FQDN hostnames, missing token/CA in manual mode.
- **`build_mcp_servers_block(cfg, env)`** assembles the `mcp_servers` payload block with `authorization_token` injected from env, `tool_configuration.allowed_tools` whitelist, missing-token warning that still emits the block (lets upstream 401 explicitly).
- **Reference workflow** at `workflows/case-studies/rogo-analyst-on-private-data.yaml` with `risk_level: high`, mandatory approval gate, eval gate, Vercel sandbox + WIF tunnel.

### Added - Admin environments API + dashboard work-queue panel

- **`/admin/environments` CRUD** in `sandcastle.api.environments_admin`. POST/GET/DELETE proxy to Anthropic `/v1/environments` with beta header `managed-agents-2026-04-01`. Tenant scoping via `metadata.sandcastle_tenant_id`. Audit hook via `engine.audit.append_audit_event`.
- **`/admin/environments/{id}/work/stats`** with 5 s in-process cache.
- **`/admin/environments/{id}/work/stream`** SSE feed polling every 2 s.
- **WorkQueuePanel** (`dashboard/src/components/runs/WorkQueuePanel.tsx`). SSE-driven panel with depth, sparkline (Recharts), pill (green < 5, amber 5-50, red > 50), `aria-live="polite"`, exponential backoff up to 30 s. 7 vitest cases.

### Added - Webhook-driven workers

- New event type **`session.status_run_started`** in `agent_webhooks.SUPPORTED_EVENTS`. Lets a worker run as a webhook handler instead of long-polling - saves RAM, latency, and your AWS bill.
- HMAC verification round-trips `sha256=` prefix and bare digest forms.

### Added - Three production case-study workflows

- **`workflows/case-studies/amplitude-design-agent.yaml`** - designer template + multiagent + accessibility-review specialist + computer-use step + Cloudflare provider.
- **`workflows/case-studies/clay-sculptor-gtm.yaml`** - project_manager coordinator + researcher / writer / qualifier specialists + Daytona snapshot sandbox + HTTP + Composio + Slack + report.
- **`workflows/case-studies/rogo-analyst-on-private-data.yaml`** - financial_analyst + Vercel + WIF tunnel + risk_level high + mandatory approval + eval gate.

### Added - Documentation

- `docs/managed-agents-self-hosted.md` (8 sections, 176 lines).
- `docs/managed-agents-mcp-tunnels.md` (158 lines).
- `docs/anthropic-2026-may-19-integration.md` (147 lines, Mermaid diagrams).

### Tested

- **150 new tests** across 10 files: self_hosted_sandbox, self_hosted_worker, mcp_tunnel, mcp_tunnel_wif, memory_mcp_server, environments_admin, agent_runtime_self_hosted, webhook_session_started, workflow_case_studies, self_hosted_cookbooks.
- **6 iterations** of rigorous validation:
  1. 5x sequential re-run - 750 / 750 deterministic
  2. Each file in isolation - 150 / 150
  3. Random order + pollution cluster - 221 / 221
  4. pytest-repeat 10x stress on the two heaviest files - 260 / 260
  5. Module import + CLI smoke across 15 modules - clean
  6. Consolidated verdict - no flake, no cross-pollution, zero new regression categories

### Versioning

PyPI: `pip install sandcastle-ai==0.32.2`

## [0.32.0] - 2026-05-16 - "Claude Agents Deep Integration"

You shipped the agent. The client wants the integration. The auditor wants the trail. The user wants you to ask the next question without restarting the whole workflow. v0.32 is the answer to every one of those. Sandcastle now exposes every Anthropic Managed Agents primitive shipped under the managed-agents-2026-04-01 beta umbrella, plus the things Anthropic doesn't ship: a cryptographically verifiable trajectory replay, a Skills publisher that turns workflows into uploadable Claude Skills, and an Agent SDK runtime for teams that want in-process execution. Two weeks of work, 169 new tests, one release.

### Added - Anthropic primitives (the things the beta header gives you)

- **Memory Stores** client (`sandcastle.engine.memory_stores.MemoryStoresClient`). Versioned per-session memory mounted at /mnt/memory/, optimistic-concurrency writes via If-Match, redact endpoint for GDPR right-to-be-forgotten, 100 kB per file, 8 stores per session. `attach_to_session_payload()` helper builds the resources block for session-create.
- **Multiagent coordinator** (`sandcastle.engine.multiagent`). Up to 20 specialist agents in parallel, 25 threads, 1-level depth per Anthropic spec. Three pre-baked templates: `research-and-write`, `code-review-and-test`, `analyst-with-translator`. `validate_roster()` + `build_coordinator_payload()` + `parse_thread_event()`.
- **Outcomes API** (`sandcastle.engine.outcomes`). `user.define_outcome` events on session start, `span.outcome_evaluation_end` captured into step output. Composite aggregator at module level so AutoPilot and Workflow Evolution can read native Anthropic eval signals.
- **Webhooks** (`sandcastle.api.agent_webhooks`). HMAC-signed session lifecycle events at `/agent-webhooks/anthropic`. Fire-and-forget dispatch, integrates with the existing arq scheduler.
- **Elicitation** (the 6th MCP primitive, added in spec rev 2025-11-25). New `request_workflow_input` tool wraps `ctx.session.elicit()` with JSON Schema validation so a workflow that hits a gap mid-execution can ask the user for a typed value without restarting.

### Added - managed-agent step extensions

The `type: managed-agent` step now accepts three new config fields that thread directly into the Anthropic primitives above:

- `memory_stores: list[str]` - attach existing memory store IDs to the session
- `multiagent: dict` - build a coordinator payload with validated roster
- `outcomes: list[dict]` - define outcomes at session start, capture eval results in step output

### Added - Sandcastle differentiators (the things Anthropic doesn't ship)

- **Skills publisher** (`sandcastle.engine.agent_skills`). `sandcastle publish-skills [--upload] [--dir]` converts every workflow into a SKILL.md tar.gz package with strict frontmatter validation (kebab-case name, no reserved tokens, ≤1024-char description) and uploads to `/v1/skills`. Workflows are now reachable from every Anthropic Skills-aware client.
- **Trajectory Replay step type** (`sandcastle.engine.trajectory_replay`). New `type: trajectory-replay` step computes SHA-256 over a recorded tool-call sequence, diffs against a candidate run, returns score + diff_summary. Because Sandcastle's audit trail is a hash chain, the replay is cryptographically verifiable - a property neither LangSmith nor Braintrust ships.
- **Computer Use integration helper** (`sandcastle.engine.computer_use`). New `type: computer-use` step type. Builds the `computer_20251124` tool definitions, sets the beta header, runs an 8-item safety pre-flight (prompt-injection guard, screenshot dimensions, page-load deadline).
- **Agent SDK runtime** (`sandcastle.engine.agent_sdk_runtime`). New `runtime: "agent-sdk"` dispatch. For teams who want in-process Claude agents (EU sovereignty, air-gapped, no Managed Agents infra). Lazy-imports `anthropic_agent_sdk`; falls back to a typed `AgentSDKNotInstalled` error when the optional package isn't installed.

### Added - Tool Search + tool-use-examples convention

New `sandcastle.engine.tool_search.ToolRegistry` lets workflows mark tools with `defer_loading: true` (loaded on first selection) and `examples: [...]` (1-5 realistic invocations per tool). Anthropic measured the result on Opus 4: tool-selection accuracy from 49% to 74%, usable context from 122,800 to 191,300 tokens (85% saving), parameter accuracy from 72% to 90%. New docs/tool-examples-convention.md.

### Added - Tier 1 wire fixes (table stakes that had been broken)

- `tools_enabled` config field is now actually sent to the agent-create API (previously parsed but ignored - users thought they were restricting tools).
- `temperature`, `max_tokens`, `thinking_budget` on `ManagedAgentConfig`. None-aware: omitted from request when unset.
- `stream` config field is now honoured (was dead code).
- Pricing table for Opus 4.7 (5/25), Sonnet 4.6 (3/15), Haiku 4.5 (1/5), Opus 4.6 (15/75), Sonnet 4.5 (3/15). Unknown model falls back to Sonnet 4.6 rates with a one-time warning.
- `fallback_template` accepts a list (chain of up to 5 templates) in addition to a single string.

### Added - dashboard

- Live "Agent Reasoning" panel on the run detail page. Subscribes to `/api/runs/{id}/agent-stream` SSE, renders agent.thinking, agent.tool_use, agent.message, agent.complete, agent.error events. Thread-grouped, collapsible, graceful 404 fallback.

### Changed

- New step types `trajectory-replay` and `computer-use` registered (VALID_STEP_TYPES count 22 -> 24).
- `agent_webhooks_router` mounted on the FastAPI app alongside `a2a_router` and `agui_router`.
- MCP server manifest now advertises 6 primitives (added Elicitation) and declares `spec_revision: "2025-11-25"`.

### Tests

- 18 new tests for Tier 1 wire fixes (tests/test_managed_agent_wires.py)
- 156 new tests for the 9 modules in isolation
- 13 new e2e wiring tests (tests/test_v032_wiring.py)
- 169 v0.32-related tests total, all green in 1.8s
- Full suite: 15,176 passing (vs 15,009 baseline) - the +167 are this release's new tests

## [0.31.0] - 2026-05-14 - "Compliance & Connections"

Eighty days to the EU AI Act deadline (2 August 2026). This release is the answer: a dedicated landing page mapping every Sandcastle control to a specific Article, ten compliance workflow templates, MCP-first publishing so every workflow becomes a tool inside Claude Desktop / Cursor / Windsurf, eval gates that block regressing models from getting promoted, and a dashboard that doesn't crash when one API hiccups. Plus the closeout of v0.30: Codex audit rounds 9 and 10 fully fixed.

### Added - EU AI Act (the deadline)
- **Dedicated EU AI Act landing page** at `/eu-ai-act/` with live countdown to 2 August 2026, three fear pillars (fines, audit, transparency), six feature blocks mapped to Articles 9, 11, 12, 14, 25, 49, 50, 73 and Annex IV, JSON-LD Organization + BreadcrumbList, GA tag, and dual CTAs.
- **10 compliance workflow templates** in `workflows/compliance-pack/`: DPIA (Article 27 + Annex IV), vendor risk assessment, incident report (Article 73), Annex IV transparency report, bias audit, human oversight log (Article 14), model card generator, risk register, GDPR data-subject request, AI inventory. Four are marked `risk_level: high` and include mandatory approval steps; six are `limited`. All parse and validate against the engine schema.
- "EU AI Act" link added to nav and footer on every site page so the landing is reachable from anywhere on sandcastle-ai.eu.

### Added - MCP-First Publishing (the connections)
- **`sandcastle publish-mcp [<workflow>]` CLI command.** No arguments: JSON-parseable list of publishable workflows. With a workflow name: ready-to-paste `mcpServers` config block for Claude Desktop (macOS / Windows), Cursor, and Windsurf, plus stderr instructions for where each client expects the snippet.
- **MCP server now ships all five primitives:** tools (existing 8 + one auto-registered per published workflow, capped at 64), resources (existing 3 + `sandcastle://roots` and `sandcastle://manifest`), prompts (new `workflow_help`), sampling (`request_llm_completion` via `ctx.session.create_message()`), and roots.
- **`.well-known/mcp.json` discovery manifest** exposed on HTTP transports (stdio clients read the manifest resource). `SANDCASTLE_PUBLISH` env var narrows the per-workflow tool list when set.
- Each published workflow becomes one MCP tool whose input schema mirrors the workflow's `input_schema`; invocations route through the existing `execute_workflow` path.

### Added - Eval Gates
- **`GoldenDataset` and `GoldenCase` DB models** (tenant-scoped, versioned, `is_active` flag, FK cascade to cases). Unique on (tenant, workflow, name, version); `expected_score_min` validated to 0..1.
- **Engine helpers `evaluate_against_golden()` and `gate_promotion()`** that replay a dataset against a workflow and return aggregate score + per-case results.
- **API endpoints:** `POST /api/golden-datasets`, `GET /api/golden-datasets/{name}`, `POST /api/workflows/{name}/eval-gate`.
- **Promotion gating:** the existing `/workflows/{name}/publish` endpoint accepts `?strict=true` to enforce the gate. With strict mode, promotion fails closed with `EVAL_GATE_FAILED` / `EVAL_GATE_DATASET_MISSING` (422). Non-strict callers unchanged.

### Added - Per-Workflow Stats API
- `GET /api/workflows/stats` (bulk) and `GET /api/workflows/{name}/stats` aggregate run counts, success rate, average cost, last-run status, and time-since-last-run. Tenant-scoped, 30s in-process cache. Dashboard workflow grid wires up to the bulk endpoint and renders real metrics per card (the previous mock generator is gone).
- `GET /api/schedules/{schedule_id}` returns a single schedule by ID (was missing; previously returned 405).

### Changed - Dashboard (the polish)
- **Overview page split** from a 2,261-line monolith into a 241-line orchestrator + 20 focused sub-components in `dashboard/src/components/overview/`. Each section is wrapped in a `SectionErrorBoundary`; a single failing fetch no longer collapses the whole page.
- A11y pass: `aria-expanded`, `aria-controls`, `aria-hidden`, `type="button"` consistently applied across Sidebar, ConfirmDialog, RunWorkflowModal, BatchRunModal, TemplatesPage, OnboardingWizard. Focus-visible rings on dialog action buttons. Sr-only labels on every search and filter input on the Runs page.
- `localStorage` reads/writes in `useTheme`, `useDashboardLayout`, `usePinnedWorkflows`, `useEventStream`, `lib/insights.ts` wrapped in try/catch with in-memory fallback so private browsing and quota-exceeded conditions degrade gracefully.
- `useEventStream` resets the reconnect attempt counter and reconnects when the API client transitions back from mock mode.
- Modal widths scale up on tablet (`md:max-w-2xl` / `md:max-w-xl`).
- Disabled button opacity bumped from 40% to 60% (WCAG-AA contrast).
- Runs table gains a horizontal scroll affordance on narrow viewports.
- Runs page skeleton row count adapts to total to avoid layout jumps.
- BatchRun poll interval extracted to a named constant.

### Security - Codex Audit Rounds 9 + 10 (5 HIGH + 1 MEDIUM, all fixed)
- A2A endpoint now honours `allowed_workflows` and per-key `max_cost_usd` budgets that restricted API keys could previously bypass.
- Mem0 scope IDs are tenant-prefixed (`tenant:<id>/workflow:<name>`); `tenant_id` threaded through every `execute_workflow` caller and step-cache key. Cross-tenant memory and cache reads can no longer happen.
- `ReportConfig` fields HTML-escaped and `accent_color` validated against a strict hex regex (stored XSS in PDF/HTML reports).
- Custom `_safe_url_fetcher` for WeasyPrint blocks private IPs and non-http(s) schemes; `logo_url` pre-validated (SSRF via `@import url(...)` and logo path).
- `list_workflow_versions` returns 404 for unknown workflows and synthesises a "disk" version for YAML-only workflows.
- Round 10: the workflow generator no longer inlines content from the shared `workflows_dir` into the LLM system prompt when a `tenant_id` is provided (cross-tenant prompt injection).

### Fixed
- Evolution cost estimator no longer assumes a hardcoded 1,000 tokens per run. It queries the last 20 completed runs for the workflow, derives tokens from `total_cost_usd` against a blended baseline, caches the result for 60 seconds, and falls back to 1,000 only when no history exists or the DB query fails. AutoPilot and Workflow Evolution now make experiment keep/discard decisions on numbers that match reality.

### Tests
- 240 MCP-related tests passing (13 new in `test_mcp_publish.py`, plus 350 across the existing MCP suite still passing).
- 266 eval-related tests passing (14 new in `test_eval_gates.py`).
- 37 compliance pack tests + 6 skips (high-risk approval requirement).
- 69 evolution tests passing (8 new in `test_evolution_tokens.py`).
- 9 new tests for the stats endpoint and tenant-scoped generator prompt.
- 784/784 dashboard vitest passing after the OverviewBento split.
- Full suite: 15,014 passing.

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
