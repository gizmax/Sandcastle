# Sandcastle code audit + fix sweep - 2026-05-31

Author: Tomas Pflanzer @gizmax

A full-codebase audit ("prověř app, zoptimalizuj, najdi nefunkční a nedodělané
věci a oprav") driven by a 12-slice parallel agent workflow with adversarial
verification, plus two CI-green blockers found while triaging PR #232.

## Method

- **Audit workflow** (`sandcastle-audit`): 12 subsystem slices audited in
  parallel, every finding then adversarially verified by an independent agent
  that re-read the code and tried to refute it. 61 agents, ~2.66M tokens.
  Result: **43 confirmed issues** (1 critical, 7 high, 16 medium, 19 low).
- Each confirmed finding carries file:line, evidence, a refined fix, and a
  safe-to-fix verdict. Findings were then triaged into *applied now* (safe,
  contained, high value) vs *deferred* (architectural / behavior-changing /
  needs its own tested change).

## CI-green blockers (PR #232 was red)

Both were pre-existing on the branch and unrelated to the tool-step feature;
they were surfaced by the failing PR checks.

### 1. `hub-validate` CI: `ModuleNotFoundError: No module named 'httpx'`
`hub-validate.yml` imports `sandcastle.engine.hub_scanner`, which triggered
`sandcastle/__init__.py`'s eager `from sandcastle.sdk import ...` -> httpx. The
validate job only installs `pyyaml jsonschema`, so the import blew up.
**Fix:** `sandcastle/__init__.py` now exposes the SDK clients lazily via PEP 562
`__getattr__`. `from sandcastle import SandcastleClient` still works, but
importing a submodule no longer drags in httpx. Verified: importing
`sandcastle.engine.hub_scanner` leaves `httpx`/`sandcastle.sdk` out of
`sys.modules`.

Fixing the import then unmasked a second hub-validate bug: the template
security scanner's pre-parse YAML-bomb heuristic counted every raw `&`/`*`
character, so 18 shipping templates (markdown emphasis, multiplication, URL
query strings) tripped a false `YAML_BOMB` error. **Fix:** `hub_scanner.py` now
counts real YAML anchor (`&name`) / alias (`*name`) tokens via regex and only
flags when anchors are actually present (no anchors -> no expansion -> no bomb).
18 false positives -> 0; a genuine 300-alias bomb still flags.

### 2. Python tests CI: ~70 cascading `no such table` failures
The in-memory test DB uses `StaticPool` (one shared connection). If that
connection is ever disposed or invalidated mid-suite, the replacement connection
sees an empty database and every later DB test fails with `no such table`.
Reproduced locally with CI parity (`pytest tests/ --timeout=60`): **70 failed /
262 `no such table`**.
**Fix (the real one):** `tests/conftest.py` registers a SQLAlchemy `connect`
listener that runs `CREATE TABLE IF NOT EXISTS` for every model on every new
in-memory connection, so the schema is guaranteed to exist on whatever
connection a session ends up using - healing the wipe at its source regardless
of cause. (Also hardened `main.py` lifespan to skip `engine.dispose()` for
in-memory SQLite, but no test drives the lifespan, so the conftest listener is
what actually fixes the suite.) Verified: the same full suite drops to 0
`no such table`.

## Fixes applied (this sweep)

| Sev | File:line | What was broken | Fix |
|-----|-----------|-----------------|-----|
| CRIT | `engine/backends.py:484` | DockerBackend called `docker.containers.create_or_run()` - a method aiodocker has never had; the Docker sandbox `AttributeError`-crashes against a real daemon, masked everywhere by mocks of the non-existent method. | Use `create()` (returns a not-yet-started container, matching the explicit `container.start()`). Repointed 9 test mocks across 3 files from `create_or_run` to `create`. |
| HIGH | `engine/dag.py:1648` | Validator rejected `lightpanda` / `browserbase` browser modes that the executor fully implements. | Expanded the allowlist + message to the five real modes. |
| HIGH | `engine/generator.py` | The new `tool` step type was undocumented in the generator's system prompt, so the LLM could never emit it. | Added a `### tool` doc block + listed `tool` among the no-prompt types. |
| MED | `engine/dag.py:1552` | Tool-step validation never checked the connector/function exist, so typos failed only at runtime. | Resolves `get_tool()` and validates `function` against the registry at parse time. |
| MED | `engine/generator.py:276` | Generator doc told the LLM to emit nonexistent `parse_config` fields (`input_path`, `output_format`). | Rewrote to the real `ParseConfig` schema (`output`, `ocr`, ...) and explained file input via the prompt. |
| MED | `engine/optimizer.py:491` | AutoPilot stats were keyed by `variant_id` (e.g. "v1"), not the model, so they never merged into the model pool keyed by model name. | Aggregate per-sample by `variant_config["model"]` in Python (DB-portable, no Postgres-only JSON operators). |
| MED | `main.py:63` | Provider pre-flight pinged a hardcoded Ollama URL for *every* keyless provider, so `omlx` was probed on the wrong port and mislabeled. | Branch per provider; derive the health URL from `settings.ollama_host` / `settings.omlx_base_url`. |
| MED | `engine/evolution.py:900` | `budget_limit` never stopped the loop: the threshold was `budget_limit * max_iterations` compared against a single run's cost. | Added a cumulative `total_spend` accumulator and stop when it exceeds `budget_limit`. |
| MED | `engine/autopilot.py:273` | The `MAX_SAMPLES_PER_EXPERIMENT` safety cap was unreachable dead code (nested inside `total < min_samples`), so experiments could sample unboundedly. | Hoisted the cap to a terminal, unconditional check; bypasses the significance gate when forced so it actually completes. |
| LOW | `engine/dag.py:1925` | `tool_config.arguments` were not scanned for `{steps.X.output}` refs, so implicit dependency ordering and unknown-ref validation both missed them. | Added tool_config to `_collect_step_template_fields`, mirroring the composio/http pattern. |

Plus the two CI-green fixes above (`__init__.py`, `main.py` lifespan).

## Deferred (verified real, but warrant their own tested change)

These are confirmed and carry refined fixes; they were not bundled into this
sweep because they change runtime behavior, touch behavior-encoding tests, or
are architectural and deserve isolated review. The full refined fixes live in
the audit workflow output.

**High:**
- `__main__.py:4334` - `--json` only accepted before the subcommand (argparse parent + `SUPPRESS` needed).
- `api/a2a.py:608` - A2A `tasks/cancel` is a no-op (never sets the backend cancel flag).
- `engine/autopilot.py:485` - deployed winner never routes traffic; `should_use_winner()` is dead, rollout is cosmetic.
- `engine/executor.py:5222` - loop/race sub-steps of hybrid types are misrouted to `sandbox.query()` instead of their real handler (needs a module-level dispatcher).
- `engine/executor.py:4225` - `computer-use` step is a permanent dry-run stub; should at least fail loudly unless dry-run is explicit.

**Medium:** `__main__.py:2936` (hub publish ignores blocking scan errors), `api/a2a.py:150` (advertises `streaming:true` with no impl), `api/a2a.py:477` (runs workflow inline, blocking), `api/routes.py:6592` (N+1 in `list_schedules`), `engine/evolution.py:231` (cost/latency score terms always 0), `engine/executor.py:7693` (hybrid types skip retry/cache/lifecycle - partly by design), `engine/executor.py:5876` (notify step never sends), `engine/policy.py:217` (per-target redaction never consumed), `engine/tools/registry.py:3618` (azure-blob account+key auth unreachable).

**Low (dead code / optimization / doc accuracy):** N+1 + blocking I/O in `routes.py:4247/4118`, blocking `getaddrinfo` in `executor.py:2777`, over-broad except in `routes.py:11063`, `config.py:126` (`memory_backend` wired to nothing), `audit.py:82` (no hash-chain tie-breaker), `dag.py:112` (`ReportConfig.template` ignored), `executor.py:2053` (step policy discards globals), `memory.py:266/507` (graph memory unused / `detect_conflicts` dead), `optimizer.py:262` (`record_outcome` never called), `pdf.py:192` + `report.py:1114/52` (dead chart paths / unused Jinja2), `sandshore.py:592` (failover event replay dup), `registry.py:2838` (optional connector env overrides unsettable), `trajectory_replay.py:13` (stale docstrings), `mcp_server.py:321` (re-parses all YAML every call).

## Test updates (tripwire + stale tests that were red on the branch)

Failing independently of the wipe, mostly because the branch added the gemini
connector + the `tool` step type without propagating the tripwire counts:
- `test_connectors_deep.py`: registry count 62->63, mjs count 63->64; added
  `tool_gemini_api_key` to `_SENSITIVE_KEYS` (routes.py) and classified
  `tool_salesforce_instance_url` as non-secret in the coverage test.
- `test_composio_deep.py`: `StepDefinition` field count 47->50.
- `test_e2e_workflows_v22.py`: all-step-types fixture gained `computer-use`,
  `tool`, `trajectory-replay` steps so it covers all 25 `VALID_STEP_TYPES`.
- `test_executor_edge_wave9.py`: cache-key test now includes the `_none_`
  tenant prefix (the key became tenant-scoped for a cross-tenant-leak fix).
- `test_executor_coverage.py`: llm-judge fallback test now patches the real
  `_call_advisor_llm` (the old patch target let a real advisor call through
  when an API key was in the env).

## Known remaining issues (2 pre-existing flakes, not from this sweep)

This sweep took the PR's Python-tests check from **82 failures to 2** (after also
merging current `main`, whose newer tests run in the PR merge ref). Both
remaining are pre-existing, order/teardown dependent, and orthogonal to the
audit work:

1. `test_workflow_e2e.py::TestRaceWorkflows::test_race_all_fail_fallback` times
   out in the full suite (it was the single e2e timeout in the original red CI).
   The **test body passes**; the timeout fires in pytest-asyncio loop
   **teardown** (`_cancel_all_tasks` blocks in `selector.select`). It does not
   reproduce under `asyncio.run` - only under pytest-asyncio's function-scoped
   loop, pointing at the in-memory-SQLite StaticPool connection (created on one
   loop, reused across per-test loops) leaving an in-flight aiosqlite op that
   the loop close waits on. A real fix likely needs a session-scoped test loop
   or a per-test engine, which is a broad test-infra change deserving its own PR.
2. `test_v028_features.py::TestBatchRunPost::test_valid_batch_returns_202` fails
   only in full-suite order (passes in isolation and in large subsets). An
   earlier test leaves module state the conftest reset does not yet cover; the
   specific polluter was not pinned within this sweep.

Both should be addressed in a dedicated test-isolation follow-up.

## Verification

- Docker backend tests (3 files): 304 passed after the `create` fix.
- dag + generator + tool-step + template validation: 113 passed; all 127
  templates validate; hub-scanner template scan: 0 errors (was 18 false
  `YAML_BOMB`).
- optimizer tests: 229 passed after the stats-keying fix.
- Full suite (CI parity, `pytest tests/ --timeout=60`): wipe healed, 0
  `no such table` (was 70 failed / 262 `no such table` before the conftest fix).
