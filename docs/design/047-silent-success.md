# 0.47 workstream 2: the silent-success sweep

A run that reports `completed` and a step that reports "1 reply created" are two
claims. Sandcastle already writes three *independent* records of what actually
happened - the step effect ledger (`run_step_effects`), the tamper-evident audit
chain (`audit_events`), and the step rows themselves (`run_steps`) - and until
now nothing has ever compared them. This is the sweep that does.

It is a **report, never a repair**. Every finding says *"claim lacks evidence"*,
which is a different sentence from *"the step failed"*. The sweep cannot know
whether the Slack message arrived; it knows only that the record which should
exist does not. Wording throughout the module, the CLI and the API keeps that
distinction, because the alternative - a sweep that asserts failure - would be
the same class of unearned confidence it exists to catch.

Module: `src/sandcastle/engine/silent_success.py`.
CLI: `sandcastle audit silent-success --since 48h`.
API: `GET /api/audit/silent-success`.

---

## 1. What the sweep can actually stand on

Everything below was read out of the tree at `121e922` before a line was
written. Anchors are `file:line` at that commit.

### The claim side - `run_steps`

`_save_run_step` (executor.py:765) upserts one row per `(run_id, step_id,
parallel_index)`. It is called from two places for a normal step - inside the
retry loop (executor.py:1922 "running", executor.py:2071 "completed") and again
from `_handle_step_result` (executor.py:9927). `RunStep` (models/db.py:168)
carries `output_data`, `status`, `parallel_index`, `replayed`, `completed_at`.

Two facts about that table drive the whole design:

- **There is no `step_type` column.** The claim side cannot tell an `http` step
  from a `notify` step from a `tool` step without going elsewhere. See §3.
- **`replayed=True` rows have no audit events.** A memoized effect returns from
  `_begin_effect_guard` inside `execute_step_with_retry` (executor.py:1824-1826)
  *before* `_execute_step_with_retry_inner` runs, and `step.started` /
  `step.completed` are emitted inside that inner function (executor.py:1945,
  executor.py:2119). The only row such a step gets is the "completed" upsert
  from `_handle_step_result`. Any audit-chain check that does not exclude
  `replayed=True` produces a false positive on **every replay, fork and
  crash-resume in the system**. This is the single largest false-positive trap
  in the feature.

### The evidence side - `run_step_effects`

`StepEffect` (models/db.py:547) is written by `EffectLedger.claim` before the
network call and settled by `commit` / `mark_failed` (effects.py:452, 471) via
`_finish_effect_guard` (executor.py:1769-1790). Row columns the sweep uses:
`effect_scope_id`, `step_id`, `parallel_index`, `step_type`, `status`.

- Scope, not run: `_begin_effect_guard` claims under
  `context.effect_scope_id or context.run_id` (executor.py:1687). A replayed run
  therefore has **no row of its own** - the committed row belongs to the parent.
  The sweep must query by `COALESCE(runs.effect_scope_id, runs.id)`, never by
  `run_id`, or every replay reads as silent.
- Not every step gets a row. `_begin_effect_guard` returns early for
  `GUARD_EXEMPT_STEP_TYPES` (effects.py:100 / executor.py:1627), for anything
  outside `_HYBRID_STEP_TYPES` (executor.py:1497, 1639), and whenever
  `effect_mode_for(step) != "memoize"` (effects.py:148, executor.py:1643) -
  which is exactly how a `GET` avoids a ledger row.
- Rows expire. `prune_expired_effects` (effects.py:649) deletes rows past
  `expires_at`, set at commit time to `now + effect_ledger_ttl_days` (default
  30, config.py:202). **Evidence older than the TTL is gone, and its absence
  proves nothing.** See §5.

### The evidence side - `audit_events`

`AuditEvent` (models/db.py:885), appended by `_emit_audit_event`
(executor.py:738) in its own session so an audit failure can never abort a run.
Nothing in the tree ever deletes audit events - unlike the ledger, the chain has
no TTL, which makes it usable arbitrarily far back.

Event types the sweep reads:

- `step.completed`, payload `{"step_id", "cost_usd", "duration_seconds"}`
  (executor.py:2113-2124).
- `step.accept`, payload carrying `step_id`, `decision`, and the full evidence
  pack (executor.py:7857-7880).

**The payloads carry `step_id` but not `parallel_index`** (executor.py:1943).
A five-way fan-out writes five `step.completed` events that are
indistinguishable from one another. Audit-chain checks are therefore
*existence* checks keyed on `step_id` alone, and can never detect a partial
fan-out silence. Stated as a non-goal in §6 rather than approximated.

---

## 2. The claim/evidence pairs

Four pairs shipped. Each names the exact claim, the exact evidence, and the
exact filter that keeps it honest.

### Pair 1 - `notify_not_in_ledger` (severity: high)

The flagship, and the closest thing in the codebase to the 330-day report's
"1 reply created" with nothing behind it.

- **Claim.** A `COMPLETED` `run_steps` row whose `output_data` is a dict with
  `status == "delivered"` and a `delivery` key. That shape is produced at
  exactly one site: the success return of `_execute_notify_step`
  (executor.py:8171-8181). It is *not* produced by the dry-run/`service: log`
  branch, which returns `status: "logged"` with `dry_run: True`
  (executor.py:8087-8093), and it is not produced by any other step type.
- **Evidence.** A `run_step_effects` row in the run's effect scope with
  `step_id` matching and `status == "committed"`.
- **Why the evidence must exist.** `notify` is in `_HYBRID_STEP_TYPES`
  (executor.py:1509), is *not* in `GUARD_EXEMPT_STEP_TYPES` (effects.py:100),
  and `effect_mode_for` returns `"memoize"` for it - it is neither a
  `_LIVE_STEP_TYPE` (effects.py:77) nor `http`, so it falls through to the
  default (effects.py:165). Every real notify delivery claims and commits.
- **Why no workflow definition is needed.** The output shape alone proves the
  step type. This pair therefore works on runs whose YAML is long gone, which
  is why it is the one pair with no resolution prerequisite. When the definition
  *is* resolvable it still wins: a notify step carrying `replay: live` is one
  the executor would not have claimed, so no row is expected even though the
  output claims delivery.

A found-but-wrong row is reported too: a ledger row that exists at `in_flight`
or `failed` while the step row says `COMPLETED` is its own inconsistency
(`effect_unsettled`, severity high) and is reported with the status actually
found rather than as an absence.

### Pair 2 - `effect_missing_from_ledger` (severity: high)

The generalisation of pair 1 to step types whose completion is a claim about
the outside world, using the run's workflow definition to decide what to expect.

- **Claim.** A `COMPLETED`, non-`replayed` `run_steps` row whose step, in the
  resolved workflow definition, has `type` in `{http, notify, tool, composio,
  openclaw, browser}` **and** `effect_mode_for(step) == "memoize"`.
- **Evidence.** Same as pair 1.
- **The GET carve-out is `effect_mode_for` itself**, not a hand-rolled method
  test: calling the real function (effects.py:148) means a `GET`, a
  `replay: live` override, and any future default change are all handled by the
  code that decides it at execution time. A step the executor would not have
  claimed is a step the sweep does not expect evidence for.
- Requires the workflow definition (§3). No definition, no pair 2.

### Pair 3 - `accept_verdict_not_on_chain` (severity: medium)

- **Claim.** A `COMPLETED` `run_steps` row whose `output_data` is the accept
  evidence pack - a dict carrying `decision`, `targets`, `rounds_used` and
  `max_rounds` together (built at executor.py:7846-7855) - with
  `decision == "approved"`.
- **Evidence.** An `audit_events` row for this run with
  `event_type == "step.accept"` and `payload["step_id"]` matching.
- **Why it holds.** `accept` is in `GUARD_EXEMPT_STEP_TYPES`, so it is never
  memoized and always executes; the audit emit at executor.py:7857 is
  unconditional on the path that produces the pack.
- Severity medium rather than high: a missing `step.accept` event means the
  verdict is unattributable, not that a side effect vanished.

### Pair 4 - `step_completed_not_on_chain` (severity: medium)

- **Claim.** A `COMPLETED`, non-`replayed` `run_steps` row for a step that the
  workflow definition says is side-effecting (same type set as pair 2, plus
  `acp` and `managed-agent`), in a run that finished at least the lag window ago.
- **Evidence.** An `audit_events` row for this run with
  `event_type == "step.completed"` and `payload["step_id"]` matching.
- Existence-only, per §1. Requires the workflow definition.

---

## 3. Resolving the workflow definition

Pairs 2 and 4 need step types, and `run_steps` has none. The established way to
recover a run's definition is `_load_versioned_workflow_yaml`
(api/routes.py:436) - the same loader replay, fork and crash-resume use
(queue/worker.py:510, imported lazily there to dodge the routes/worker import
cycle; the sweep does the same). It prefers the pinned `WorkflowVersion` row
(models/db.py:850) and falls back to disk, then to the template catalog.

Two rules keep the fallback from manufacturing findings:

1. **A step id absent from the resolved definition is never a finding.** It is
   counted as `unresolved_steps` in the report meta. Drifted YAML means we do
   not know what that step was, and not knowing is not evidence.
2. **The definition gates expectation, never the claim.** It is used only to
   answer "should there be a ledger row for this?". A claim always comes from a
   record the run itself wrote.

When the definition cannot be loaded at all, the run is still swept - pairs 1
and 3 need no definition - and the run is listed in `meta.definition_unresolved`
so the operator can see the sweep ran at reduced coverage rather than clean.

---

## 4. Matching a step row to a ledger row

Both sides carry `parallel_index`, and they agree by construction:
`execute_step_with_retry` passes the same value to `_begin_effect_guard`
(executor.py:1824) and to `_save_run_step` (executor.py:1922). So the strict
match is `(effect_scope_id, step_id, parallel_index)`.

Two shapes complicate it, both verified:

- **Fan-out** writes N per-item rows with `parallel_index = 0..N-1` *and* one
  aggregate row with `parallel_index = NULL` whose `output_data` is a **list**
  (executor.py:10119-10145). The aggregate never trips a claim detector, because
  every detector requires a dict.
- **Loop iterations** upsert a single row with `parallel_index = NULL` while the
  ledger writes one row per `iteration_index`, also with
  `parallel_index = NULL`. Existence still matches.

**Invented decision - the loose fallback.** A strict miss falls back to matching
on `(effect_scope_id, step_id)` with `parallel_index` ignored. If the loose match
finds a committed row, **no finding is emitted**; the case is counted in
`meta.suppressed_loose_match`. This deliberately gives up detecting "4 of 5
fan-out items delivered" in exchange for never inventing a finding out of an
index mismatch we did not foresee. The counter exists so the trade is visible
instead of silent - if it is ever non-zero in practice, that is the signal to
revisit.

---

## 5. Windows: `--since`, the lag, and the TTL ceiling

Three time bounds, each for a different reason.

- **`--since` / `since`** picks the runs. Default `24h`. Parsed by
  `timemachine.parse_since` (engine/timemachine.py:114), the same helper the
  time-machine CLI and the audit list endpoint already use - so `48h`, `7d`,
  `2w` and ISO datetimes all work with no new syntax.
- **The evidence-lag window** (`settings.silent_success_lag_hours`, default
  `1.0`). A claim whose step finished less than this long ago is not flagged:
  evidence may still be in flight. Strictly, the ledger commit in
  `_finish_effect_guard` is synchronous with the step, so the real lag for
  pairs 1 and 2 is near zero - but `_emit_audit_event` runs in its own session
  and the run row is written by the worker afterwards (queue/worker.py:207), so
  a run observed mid-teardown genuinely can be missing records it is about to
  write. The lag applies uniformly to all four pairs rather than per-pair,
  because a per-pair lag is a knob nobody would tune correctly. Suppressed
  claims are counted in `meta.within_lag_window`.
- **The TTL ceiling.** Ledger evidence is pruned at
  `effect_ledger_ttl_days` (config.py:202), so for pairs 1 and 2 a step older
  than that has no evidence *by design*. Those steps are **not flagged**; they
  are counted in `meta.beyond_effect_ttl`. A `--since 90d` sweep on a default
  install therefore reports honestly on the last 30 days of ledger pairs and on
  the full 90 days of chain pairs, instead of reporting 60 days of fiction.

**Invented decision - the dry-run disclaimer (added by implementation).** The
first draft of pair 2 flagged the `service: log` notify step in the honest test
fixture, and it was right on the mechanics: the effect guard runs *before*
`_execute_notify_step` knows it is a dry run, so a real dry-run notify does get
a committed ledger row, and its absence really is a bookkeeping gap. It is not a
*silent success*. `dry_run: True` in the output is the step explicitly saying it
changed nothing, and a step that asserts nothing has no claim to be unbacked -
flagging it would fill the report with exactly the runs people set `dry_run` on
in order to test safely. `disclaims_side_effect` therefore excludes such rows
from both ledger pairs (never from the chain pairs: the step still ran, and
should still be on the chain). The test that caught this is
`test_dry_run_notify_claims_nothing`.

**Invented decision - the kill-switch guard.** When
`settings.effect_ledger_enabled` is false, pairs 1 and 2 are skipped entirely
and `meta.ledger_enabled` is `false`. With the ledger off there is no evidence
side, and a check with no evidence side is not a check.

**Invented decision - zero-coverage downgrade.** If a run has **no** committed
ledger rows at all in its scope while two or more of its steps expected one,
that pattern is far more likely to be "the ledger was unreachable for this run"
(the `unavailable` claim outcome, effects.py:523, which executes live and
records nothing) than "every side effect in this run was silent". Findings from
such a run keep their type but drop one severity level and carry
`ledger_coverage=none` in their detail. They are still reported - an
unreachable ledger during a run *is* something an operator should see - but they
do not drown the report at high severity.

---

## 6. What the sweep deliberately does NOT check, and why

- **Whether the notification actually arrived.** Out of reach. The connector's
  return value is the only delivery record and it is what the step already
  reported; re-reading it would be checking a claim against itself.
- **Partial fan-out silence.** Ruled out by the loose fallback (§4) and by the
  audit payload having no `parallel_index` (§1). Documented cost of the
  false-positive trade.
- **`accept` "evidence pack present and non-empty in the step output".** This
  was on the candidate list and is **rejected**: it compares the output to
  itself. The pack is written by the same return statement that sets
  `status="completed"` (executor.py:7885-7891), so it can never be absent when
  the claim is present, and a check that cannot fail is not a check. Pair 3
  checks the pack against the *audit chain* instead, which is a genuinely
  independent store.
- **`http` GET steps and any step with `replay: live`.** No ledger row is
  written for them by design (effects.py:148). Excluded by calling
  `effect_mode_for` rather than by a hand-rolled method list.
- **`llm` steps.** They memoize and do get ledger rows, so the check would
  mechanically work - but an `llm` step's claim is "I produced text", not "I
  changed the world", and its output is right there in the row. Including it
  would triple the report volume with findings nobody would action. Out of
  scope, not impossible.
- **Deleted / expired evidence.** §5.
- **`gate`, `condition`, `classify`, `approval`, `loop`, `race`,
  `sub_workflow`, `delegate`.** `GUARD_EXEMPT_STEP_TYPES` - they never claim an
  effect, so an absent ledger row is correct, not silent.
- **`SKIPPED` and `AWAITING_APPROVAL` step rows.** Branch-skipped steps get a
  row via `_save_run_step(status="skipped")` (executor.py:9877) and no audit
  event; an approval writes `awaiting_approval` (executor.py:3293) and no audit
  event. Neither claims anything. Only `COMPLETED` rows are read.
- **Steps skipped by replay.** They get no row at all (executor.py:10720-10725),
  so they cannot be flagged.
- **Runs that are not `COMPLETED`.** A `FAILED` or `PARTIAL` run is not claiming
  success; its steps' inconsistencies are already visible in the run status.
  `--all-statuses` is deliberately not offered.
- **Auto-repair, re-delivery, re-queueing.** Never. The sweep has no write path
  of any kind.

---

## 7. Surfaces

**Library.** `sweep_runs(...) -> SweepReport` in `engine/silent_success.py`,
with `SilentSuccessFinding` as a frozen dataclass carrying `run_id`, `step_id`,
`parallel_index`, `finding_type`, `claim`, `expected_evidence`, `found`,
`severity`, `detail`, `workflow_name`, `occurred_at`. `sweep_run(run_id)` is the
single-run entry point; both share `_findings_for_run`.

**API.** `GET /api/audit/silent-success` - tenant-scoped through
`get_tenant_id(request)` on `Run.tenant_id` under
`settings.auth_required and tenant_id is not None`, exactly like
`list_violations` (api/routes.py:9397), which is the endpoint whose shape this
one copies. Not admin-only: unlike `/api/audit`, this returns nothing a tenant
cannot already read from its own runs.

**CLI.** `sandcastle audit silent-success --since 48h [--run <id>] [--json]`,
hung off the existing `audit` subparser (`__main__.py`:5892) next to
`audit verify`. It goes through `_api_get` (`__main__.py`:3841), which is the
convention for every DB-reading command in the CLI, and prints a `_table`.
**Exit code 1 when findings exist**, matching `audit verify`'s FAIL behaviour,
so a cron wrapper needs no jq.

**Scheduled sweep: deliberately not built.** There is no periodic hook to hang
it on cheaply. `queue/scheduler.py` is an APScheduler instance dedicated to
user-defined workflow `Schedule` rows, and the worker's only recurring hook is
`startup` (queue/worker.py:769) - a one-shot, which is where
`prune_expired_effects` lives. Adding a cron entry would mean either a new
APScheduler job with its own multi-worker coordination story or an arq cron
table that does not exist yet. Out of scope for this workstream; the CLI is
cron-able today. Follow-up noted in §9.

---

## 8. Config

`settings.silent_success_lag_hours: float = 1.0` (config.py, next to the effect
ledger block). Env `SILENT_SUCCESS_LAG_HOURS`. Claims from steps that finished
less than this many hours ago are counted, not flagged. `0` disables the window.
Overridable per call via the `lag_hours` argument and the `lag_hours` query
parameter, which is how the tests exercise both sides of the boundary.

No migration. The sweep is pure reads over three existing tables.

---

## 9. Follow-ups

- A scheduled sweep, once there is a periodic worker hook worth hanging it on.
- Partial fan-out detection, if `meta.suppressed_loose_match` is ever non-zero
  in the field.
- `parallel_index` in the `step.started` / `step.completed` audit payloads would
  make pair 4 exact instead of existence-only. One-line change in
  `_started_payload` / `_completed_payload`; not made here because it changes
  the hash chain's payload shape and belongs with whoever owns the chain.
- A dashboard surface. The endpoint is shaped for one; nothing consumes it yet.

---

## 10. Tests

`tests/test_silent_success_047.py`. The definition-of-done is
`TestHonestVersusSilent::test_sweep_finds_only_the_silent_run`: two runs of the
same workflow, both executed for real through `execute_workflow` against the
test database (httpx and `dispatch_webhook` patched so nothing leaves the
process, everything else - the effect guard, the ledger, the audit chain, the
accept checks - is the production path), one left alone, one tampered with by
deleting exactly one committed ledger row. One finding, on the tampered run.

The honest fixture carries the traps on purpose: a GET (no ledger row by
design), a dry-run notify (claims nothing), and a checks-only accept.
`TestReplayIsNotSilent` runs the same workflow twice in one effect scope and
asserts the memoized second run produces nothing - that is the check that would
otherwise flag every replay in the system.

Every hand-crafted fixture is a *targeted* mutation of a real run - delete one
ledger row, delete one audit event, flip one status to `in_flight`, move one
`parallel_index` - and each says so in its docstring. Nothing in the file
inserts a fabricated run from scratch.
