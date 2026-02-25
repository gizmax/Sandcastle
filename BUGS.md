# Executor Bug Report

Discovered during multi-pass executor bug hunt (2026-02-25).

Items here are real issues that require architectural changes or are low-risk
in the current deployment model. Isolated, safe fixes were applied directly.

---

## CRITICAL

### BUG-001: `apply_variant()` dropped 20+ StepDefinition fields
- **File:** `src/sandcastle/engine/autopilot.py:125`
- **Description:** Only 11 of 34 fields were copied. SLO, model_pool, tools,
  memory, policies, and all type-specific configs silently dropped.
- **Root cause:** Manual `StepDefinition(...)` constructor instead of
  `dataclasses.replace()`.
- **Status:** FIXED in this PR. Regression test in `TestFieldPreservation`.

### BUG-002: Cache key ignored resolved input context
- **File:** `src/sandcastle/engine/executor.py:1052`
- **Description:** Key = `workflow:step:model:raw_template`. Two runs with
  different upstream outputs (e.g. `{steps.prev.output}`) produced identical
  cache keys, returning stale results.
- **Root cause:** `_compute_cache_key` received the template string, not the
  resolved prompt.
- **Status:** FIXED in this PR. Regression test in `TestCacheKeyResolved`.

### BUG-003: `step_overrides` in `execute_step_with_retry` dropped fields
- **File:** `src/sandcastle/engine/executor.py:700`
- **Description:** Fork override applied via manual constructor, dropping
  tools, memory, SLO, policies, all type-specific configs (same root cause
  as BUG-001).
- **Status:** FIXED in this PR. Regression test in `TestFieldPreservation`.

### BUG-004: Policy resolution in `_prepare_and_run_step` dropped fields
- **File:** `src/sandcastle/engine/executor.py:3774`
- **Description:** Both branches (assign global policies / merge step
  policies) used manual constructor, dropping tools, memory, SLO, etc.
- **Status:** FIXED in this PR. Uses `dataclasses.replace()` now.

---

## MEDIUM

### BUG-005: Budget overshoot on parallel step launch
- **File:** `src/sandcastle/engine/executor.py` - DAG scheduler loop
- **Description:** Budget check runs BEFORE launching ready steps, not after
  cost accrual. Multiple parallel steps can collectively exceed budget before
  any completes and reports cost.
- **Root cause:** Architectural - no pre-flight cost reservation.
- **Why deferred:** Requires changes to scheduler loop and budget model.
  Pre-flight estimation or post-completion reconciliation both non-trivial.
- **Impact:** Overshoot up to `(N-1) * max_step_cost` with N parallel steps.
- **Suggested fix:** Reserve estimated cost before launch, or add
  post-completion budget check that cancels remaining steps.

### BUG-006: DLQ `_send_to_dead_letter` swallowed exceptions
- **File:** `src/sandcastle/engine/executor.py:4700`
- **Description:** Bare `except Exception` logged error but returned None.
  Callers had no way to know DLQ insert failed.
- **Root cause:** Missing return value on error path.
- **Status:** FIXED in this PR. Returns `bool`; callers log warning on False.

### BUG-007: `_escape_js_string` insufficient for shell single-quote context
- **File:** `src/sandcastle/engine/executor.py:378`
- **Description:** Escapes `'` to `\'`, which is correct for JS strings but
  NOT for POSIX shell single-quoted strings (no escape mechanism exists inside
  single quotes). Action scripts passed via `node -e '...'` (lines 3573, 3768)
  could theoretically break out of the shell quoting.
- **Root cause:** JS escaping conflated with shell escaping.
- **Why deferred:** Execution is inside an isolated sandbox (E2B/Docker), and
  the LLM already has direct shell access there, so no privilege escalation.
  The heredoc approach (lines 3251, 3404) is safe.
- **Impact:** Low - sandbox-contained, no host exposure. The `node -e` paths
  receive LLM-generated tool_input, not direct user input.
- **Suggested fix:** Replace `node -e '...'` calls with the heredoc/temp-file
  pattern already used elsewhere, or use `shlex.quote()` around the full
  script argument.

---

## LOW

### BUG-008: No backoff jitter in retry delay
- **File:** `src/sandcastle/engine/executor.py:235` - `_backoff_delay()`
- **Description:** Deterministic `2^attempt` with no random variance.
  Thundering herd risk in future distributed deployments.
- **Root cause:** Not implemented.
- **Why deferred:** Single-process executor, no shared backend contention.
- **Suggested fix:** `delay = min(2**attempt, 60) + random.uniform(0, 1)`

### BUG-009: `branch_skip_steps` concurrent mutation
- **File:** `src/sandcastle/engine/executor.py` - condition/classify handlers
- **Description:** Set mutated by step handlers, read by DAG scheduler.
  Technically a data race, but safe under CPython GIL + asyncio cooperative
  scheduling.
- **Root cause:** No synchronization primitive.
- **Why deferred:** Safe in current runtime. Becomes a bug only under
  free-threaded Python (PEP 703) or true thread parallelism.
- **Suggested fix:** Wrap mutations in `asyncio.Lock`, or document GIL
  dependency.

### BUG-010: Fan-out child contexts share parent `costs` list
- **File:** `src/sandcastle/engine/executor.py:79` - `RunContext.with_item()`
- **Description:** `costs=self.costs` passes the SAME list object to child
  contexts. Currently safe because costs are only appended from the parent's
  gather loop (line 4157), not from within child execution paths.
- **Root cause:** Intentional sharing for efficiency, but fragile design.
- **Why deferred:** Works correctly today. Would break if any future code path
  appends costs from within a child context.
- **Suggested fix:** Copy the list in `with_item()` and merge after gather.
