# Known Issues & Deferred Bugs

Discovered during executor bug hunt (2026-02-25). Items here are real issues
that require architectural changes or are low-risk in the current deployment
model.

---

## 1. Budget overshoot on parallel step launch

**Severity:** Medium
**File:** `src/sandcastle/engine/executor.py` - DAG scheduler loop

**Problem:** The budget check runs BEFORE launching ready steps, not after cost
accrual. When multiple steps become ready simultaneously, they are all launched
in parallel. Each step can consume budget, and collectively they may exceed the
configured budget limit before any of them completes and reports its cost back.

**Impact:** Workflows with tight budgets and many parallel steps can overshoot
the budget cap by up to `(N-1) * max_step_cost` where N is the number of
concurrent steps.

**Suggested fix:** Either pre-flight cost estimation (reserve estimated cost
before launch) or a post-completion reconciliation check that halts remaining
steps when the budget is exceeded. Both approaches require changes to the
executor scheduling loop and the budget tracking data model.

---

## 2. No backoff jitter in retry delay

**Severity:** Low
**File:** `src/sandcastle/engine/executor.py` - `_backoff_delay()`

**Problem:** The backoff delay is deterministic: `2^attempt` seconds with no
random jitter. If multiple workflow runs fail and retry at the same time, they
will all retry at identical intervals, causing a thundering herd.

**Impact:** Negligible in the current single-process executor. Becomes relevant
if Sandcastle moves to distributed execution with shared backend resources.

**Suggested fix:** Add random jitter: `delay = (2 ** attempt) + random.uniform(0, 1)`.

---

## 3. `branch_skip_steps` concurrent mutation

**Severity:** Low (safe under CPython GIL)
**File:** `src/sandcastle/engine/executor.py` - condition/classify handlers

**Problem:** The `branch_skip_steps` set is mutated by condition and classify
step handlers and read by the DAG scheduler. In theory this is a data race, but
in practice CPython's GIL and the sequential condition evaluation within
`asyncio` event loop make this safe.

**Impact:** None under CPython. Could become a bug if the executor is ported to
a free-threaded Python runtime (PEP 703) or uses true thread parallelism.

**Suggested fix:** Wrap mutations in a lock, or document the GIL dependency
explicitly in the code.
