# Reconstructed design notes: `type: accept`

The full design doc (scratchpad `045/C-accept-gate-design.md`, ~1050 lines) was
lost to a tmp sweep. These are the verified findings it contained, reconstructed
from the design agent's final report. Line numbers are from the 0.45-era tree
and have drifted — treat them as search anchors, and re-verify each before
relying on it. Anything not listed here is the implementer's decision, to be
flagged explicitly for review.

## Verified findings (with original anchors)

- **`eval.check_assertion` (eval.py:158) is a pure async function over a
  value** — it can score a step's output mid-run with zero new code. `accept`'s
  `checks:` are literally `AssertionDef`.
- **The eval-harness LLM-judge path is UNUSABLE for accept.**
  `eval._check_llm_judge` → `autopilot._evaluate_llm_judge` →
  `generator._call_advisor_llm` returns **only text** (generator.py:1319); its
  cost is character-estimated into a side audit event and discarded; it cannot
  take a per-judge model; it **fabricates a 0.5 score on error**
  (autopilot.py:228). Judges must use the gate-style direct HTTP path, which
  accounts cost via `_safe_cost` (executor.py:~6575 pre-0.45 numbering).
- **`evals.py` is the wrong module entirely** — it replays whole workflows
  against golden datasets (evals.py:113); unusable mid-run.
- **Free reuse:** human fallback via `_execute_approval_step`
  (executor.py:~2853) + `/approvals/*` routes + the scheduler timeout sweeper
  (scheduler.py:~440) + dashboard; in-process re-run pattern in
  `_execute_loop_step` (executor.py:~6057); `step_overrides` prompt injection
  (executor.py:~1540); JSONB output + artifacts dir + SHA-256 chain
  (audit.py:116).
- **One small API change needed:** an `_on_approve` overlay in
  `_resume_after_approval` (routes.py:~9092) — approve resumes with
  `request_data` verbatim while reject fails the run outright (routes.py:~8814).
- **Cost visibility:** judges default to ONE, quorum defaults to UNANIMOUS,
  `checks` run first and can reject for $0. The pre-run estimator
  (routes.py:~3853) must sum per-judge pricing × `(1 + max_rounds)`.
- **The retry loop is bounded four ways:** rounds ≤5 hard cap, accept-local
  budget, run-budget projection (pattern copied from executor.py:~6076), depth
  guard — plus two validate-time structural bans (no self-target, no cycle).
  Judge critique must be `_escape_braces`'d before re-injection or a judge can
  inject templates.
- **Landmine:** on reject the StepResult must set `retryable=False`
  (executor.py:78) or a step-level `retry:` silently re-judges 3× and can flip
  the verdict by luck.
- **Sizing:** ~450-600 new engine lines, no DB migration.

## Post-0.45 ground truth (verified 2026-08-25, current tree)

- Rejected gates now FAIL the step (`_gate_rejection`, `fail_on_reject`) —
  accept's rejection must match that shape.
- `GUARD_EXEMPT_STEP_TYPES` = {approval, classify, condition, delegate, gate,
  loop, race, sub_workflow}. `gate_config` is NOT in `_CONFIG_ATTRS`.
  Decision (grounded by the accept implementer, endorsed): **accept joins
  GUARD_EXEMPT_STEP_TYPES** — it blocks like `gate` and recurses into a target
  that is itself guarded; therefore `accept_config` stays OUT of
  `_CONFIG_ATTRS`, per the gate precedent.
- `accept` must be added to `_HYBRID_STEP_TYPES` with a membership test.
