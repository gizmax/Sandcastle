# Reconstructed design notes: crash-resume

From the 0.45 replay-idempotency design (scratchpad `045/B-…`, lost to a tmp
sweep); crash-resume was its named follow-up. Anchors are 0.45-era; re-verify.

- **Resume machinery is type-agnostic and correct:** checkpoints persist the
  full `step_outputs` dict (`RunContext.snapshot()`, executor.py:176-188 →
  `run_checkpoints.context_snapshot`); `done_steps = set(skip_steps)`
  (executor.py:~9411); the skip set comes from the checkpoint's `step_outputs`
  keys (routes.py:~7123/7265/9099).
- **Known hole:** in-flight steps cancelled at a pause re-fire on resume —
  they never wrote a `step_outputs` entry, so they are absent from
  `skip_steps` (executor.py:~9539-9541). The ledger's `in_flight` claim +
  `on_uncertain` is the 0.45 answer for side-effecting types.
- **No crash-resume exists:** `_recover_stuck_runs` marks crashed runs FAILED
  (worker.py:369-412).
- **Effect scope propagation precedent:** replay and fork set
  `effect_scope_id = parent.effect_scope_id or parent.id` (routes.py). Recovery
  must reuse the run's own scope so the ledger memoizes the completed prefix.
- **UNVERIFIED items from the design (verify before relying):**
  `step_results` is not restored on resume (may break `{steps.X.status}`);
  skipped steps produce no RunStep rows on replay; no StepCache sweeper;
  race/sub_workflow child-context distinguishability.
- **Migration discipline:** head is `022`; next is `023`. Verify with
  `alembic heads` immediately before writing; `test_alembic_single_head` must
  stay green. This repo had a three-way collision at `019`.
