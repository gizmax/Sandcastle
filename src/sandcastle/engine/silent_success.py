"""Silent-success detector: cross-check what a run *claimed* against evidence.

The worst production failure is not a run that fails. It is a run that reports
``completed`` with a step that reports "1 reply created" when nothing was ever
sent. Sandcastle already writes three independent records of what happened -
the step effect ledger (``run_step_effects``), the tamper-evident audit chain
(``audit_events``), and the step rows themselves (``run_steps``) - and nothing
has ever compared them. This module does.

Four claim/evidence pairs, all read-only (see docs/design/047-silent-success.md
for the full derivation, the rejected pairs, and the false-positive analysis):

1. ``notify_not_in_ledger`` - a step output claiming ``status: delivered`` with
   no committed ledger row for the effect. The output shape is produced at
   exactly one site (``_execute_notify_step``), so this pair needs no workflow
   definition and works on runs whose YAML is long gone.
2. ``effect_missing_from_ledger`` - the same check generalised to every
   side-effecting step type the executor would have claimed, decided by calling
   the executor's own ``effect_mode_for`` rather than guessing.
3. ``accept_verdict_not_on_chain`` - an ``accept`` evidence pack claiming
   ``approved`` with no ``step.accept`` event on the audit chain.
4. ``step_completed_not_on_chain`` - a side-effecting step marked COMPLETED with
   no ``step.completed`` event on the audit chain.

Three rules hold the whole thing together:

**Flag, do not guess.** A finding says the claim *lacks evidence*. It never says
the step failed - the sweep cannot know whether the Slack message arrived, only
that the record which should exist does not.

**Never repair.** There is no write path in this module. Not one.

**Absence of evidence is only evidence of absence when the evidence should
still be there.** Ledger rows are pruned at ``effect_ledger_ttl_days``; claims
older than that are counted, never flagged. Claims younger than
``silent_success_lag_hours`` are counted, never flagged, because the evidence may
still be in flight. And a memoized (``replayed``) step is skipped entirely: it
fired no effect this time, and its cassette variant leaves no ledger row at all.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Step types whose completion is a claim about the outside world *and* which the
# effect guard claims in the ledger. Every member is in the executor's
# _HYBRID_STEP_TYPES and none is in GUARD_EXEMPT_STEP_TYPES, so a committed row
# is genuinely expected - subject to effect_mode_for, which is what carves out a
# GET or an explicit ``replay: live``.
LEDGER_CLAIM_STEP_TYPES = frozenset(
    {"http", "notify", "tool", "composio", "openclaw", "browser"}
)

# Additionally checked against the audit chain. ``acp`` and ``managed-agent``
# drive an external harness, so a completion with nothing on the chain is worth
# surfacing even though their ledger story is the generic one above.
CHAIN_CLAIM_STEP_TYPES = LEDGER_CLAIM_STEP_TYPES | {"acp", "managed-agent"}

SEVERITY_ORDER = ("low", "medium", "high")

_DEFAULT_SINCE = "24h"
_DEFAULT_LIMIT = 500


def _downgrade(severity: str) -> str:
    """Drop one severity level, floored at ``low``."""
    try:
        idx = SEVERITY_ORDER.index(severity)
    except ValueError:
        return severity
    return SEVERITY_ORDER[max(idx - 1, 0)]


@dataclass(frozen=True)
class SilentSuccessFinding:
    """One claim that is not backed by the evidence it should have produced.

    Deliberately not a verdict: ``found`` records what the sweep actually saw,
    and the wording of ``claim`` / ``expected_evidence`` keeps the finding a
    question for an operator rather than an assertion about the world.
    """

    run_id: str
    workflow_name: str
    step_id: str
    parallel_index: int | None
    finding_type: str
    claim: str
    expected_evidence: str
    found: str
    severity: str
    detail: str = ""
    occurred_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "step_id": self.step_id,
            "parallel_index": self.parallel_index,
            "finding_type": self.finding_type,
            "claim": self.claim,
            "expected_evidence": self.expected_evidence,
            "found": self.found,
            "severity": self.severity,
            "detail": self.detail,
            "occurred_at": (
                self.occurred_at.isoformat() if self.occurred_at is not None else None
            ),
        }


@dataclass
class SweepReport:
    """Findings plus the counters that say what the sweep could not check.

    The counters are not decoration. A sweep that reports zero findings because
    it silently skipped everything would be the same failure mode the sweep
    exists to catch, so every suppression is counted and surfaced.
    """

    findings: list[SilentSuccessFinding] = field(default_factory=list)
    runs_checked: int = 0
    steps_checked: int = 0
    ledger_enabled: bool = True
    since: datetime | None = None
    lag_hours: float = 0.0
    # Suppression counters
    within_lag_window: int = 0
    beyond_effect_ttl: int = 0
    replayed_skipped: int = 0
    unresolved_steps: int = 0
    suppressed_loose_match: int = 0
    definition_unresolved: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def meta(self) -> dict[str, Any]:
        return {
            "runs_checked": self.runs_checked,
            "steps_checked": self.steps_checked,
            "findings": len(self.findings),
            "ledger_enabled": self.ledger_enabled,
            "since": self.since.isoformat() if self.since is not None else None,
            "lag_hours": self.lag_hours,
            "within_lag_window": self.within_lag_window,
            "beyond_effect_ttl": self.beyond_effect_ttl,
            "replayed_skipped": self.replayed_skipped,
            "unresolved_steps": self.unresolved_steps,
            "suppressed_loose_match": self.suppressed_loose_match,
            "definition_unresolved": self.definition_unresolved,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "meta": self.meta(),
        }


# ---------------------------------------------------------------------------
# Claim detectors - what a step *output* asserts
# ---------------------------------------------------------------------------


def is_notify_delivery_claim(output: Any) -> bool:
    """Whether *output* is a notify step asserting real delivery.

    The success return of ``_execute_notify_step`` is the only site in the tree
    producing ``status: "delivered"`` alongside a ``delivery`` payload. The
    dry-run / ``service: log`` branch returns ``status: "logged"`` with
    ``dry_run: True`` instead, and asserts nothing about the world - so it is
    not a claim and is never flagged.
    """
    if not isinstance(output, dict):
        return False
    if output.get("dry_run"):
        return False
    return output.get("status") == "delivered" and "delivery" in output


def disclaims_side_effect(output: Any) -> bool:
    """Whether *output* explicitly says the step changed nothing.

    ``_execute_notify_step`` sets ``dry_run: True`` on the branch that logs
    instead of delivering. The effect guard still claims and commits a ledger
    row for such a step - it runs before the step knows it is a dry run - so a
    missing row there is a real bookkeeping gap. It is not a *silent success*:
    the step asserted nothing about the world, so there is no claim to be
    unbacked. Flagging it would fill the report with the exact runs people set
    ``dry_run`` on in order to test safely.
    """
    return isinstance(output, dict) and bool(output.get("dry_run"))


def is_accept_approval_claim(output: Any) -> bool:
    """Whether *output* is an ``accept`` evidence pack recording an approval.

    Matches the pack built in ``_execute_accept_step``: ``decision`` together
    with ``targets``, ``rounds_used`` and ``max_rounds``. All four are required
    because ``decision`` alone is a common key in workflow data.
    """
    if not isinstance(output, dict):
        return False
    if output.get("decision") != "approved":
        return False
    return all(k in output for k in ("targets", "rounds_used", "max_rounds"))


# ---------------------------------------------------------------------------
# Evidence lookups
# ---------------------------------------------------------------------------

_LEDGER_COMMITTED = "committed"

_MATCH_COMMITTED = "committed"
_MATCH_UNSETTLED = "unsettled"
_MATCH_LOOSE = "loose"
_MATCH_MISSING = "missing"


def _match_ledger(rows: list[Any], parallel_index: int | None) -> tuple[str, str]:
    """Match a step row against the ledger rows recorded for its step id.

    Both sides carry ``parallel_index`` and they agree by construction
    (``execute_step_with_retry`` passes the same value to the effect guard and
    to ``_save_run_step``), so the strict match uses it. A strict miss with a
    committed row somewhere else under the same step id is reported as
    ``loose``: it is suppressed rather than flagged, because an index mismatch
    we did not foresee must not become a finding. The cost - no detection of a
    partial fan-out silence - is a deliberate trade, counted in the report.
    """
    if not rows:
        return _MATCH_MISSING, ""

    strict = [r for r in rows if r.parallel_index == parallel_index]
    if strict:
        if any(r.status == _LEDGER_COMMITTED for r in strict):
            return _MATCH_COMMITTED, _LEDGER_COMMITTED
        return _MATCH_UNSETTLED, strict[0].status
    return _MATCH_LOOSE, ""


# ---------------------------------------------------------------------------
# Workflow definition resolution
# ---------------------------------------------------------------------------


async def _resolve_step_definitions(run: Any) -> dict[str, Any] | None:
    """Return ``{step_id: StepDefinition}`` for *run*, or None when unresolvable.

    Uses ``_load_versioned_workflow_yaml`` - the same loader replay, fork and
    crash-resume use - which prefers the ``WorkflowVersion`` row the run was
    pinned to and falls back to disk and then the template catalog. The import
    is lazy for the same reason ``queue/worker.py`` does it lazily: routes
    imports the worker, so a module-level import would be circular.

    A definition that cannot be loaded is not an error. It costs the two pairs
    that need step types and leaves the two that do not.
    """
    try:
        from sandcastle.api.routes import _load_versioned_workflow_yaml
        from sandcastle.engine.dag import parse_yaml_string

        yaml_content = await _load_versioned_workflow_yaml(
            run.workflow_name, run.workflow_version
        )
        workflow = parse_yaml_string(yaml_content)
    except Exception as exc:  # noqa: BLE001 - an unresolvable definition is data
        logger.debug(
            "Silent-success sweep could not resolve workflow '%s' for run %s: %s",
            run.workflow_name,
            run.id,
            exc,
        )
        return None
    return {s.id: s for s in workflow.steps}


def _expects_ledger_row(step_def: Any) -> bool:
    """Whether the effect guard would have claimed a ledger row for *step_def*.

    ``effect_mode_for`` is called rather than reimplemented: it is the function
    the executor consults at execution time, so a GET, an explicit
    ``replay: live`` and any future change to the defaults are all handled by
    the one place that decides them.
    """
    from sandcastle.engine.effects import GUARD_EXEMPT_STEP_TYPES, effect_mode_for

    step_type = getattr(step_def, "type", "")
    if step_type not in LEDGER_CLAIM_STEP_TYPES:
        return False
    if step_type in GUARD_EXEMPT_STEP_TYPES:
        return False
    return effect_mode_for(step_def) == "memoize"


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def _claim_time(step_row: Any, run: Any) -> datetime:
    """When the claim was made, for the lag and TTL windows.

    Prefers the step's own completion, falls back to the run's, then to the run
    row itself - which always exists.
    """
    for candidate in (step_row.completed_at, run.completed_at, run.created_at):
        if candidate is not None:
            if candidate.tzinfo is None:
                return candidate.replace(tzinfo=timezone.utc)
            return candidate
    return datetime.now(timezone.utc)


def _findings_for_run(
    run: Any,
    step_rows: list[Any],
    ledger_by_step: dict[str, list[Any]],
    chain_step_ids: set[str],
    accept_step_ids: set[str],
    step_defs: dict[str, Any] | None,
    report: SweepReport,
    *,
    lag_cutoff: datetime,
    ttl_cutoff: datetime | None,
) -> list[SilentSuccessFinding]:
    """Cross-check one completed run's claims against its evidence."""
    findings: list[SilentSuccessFinding] = []
    run_id = str(run.id)
    ledger_pending: list[SilentSuccessFinding] = []
    expected_ledger_rows = 0

    for step_row in step_rows:
        report.steps_checked += 1
        output = step_row.output_data
        step_id = step_row.step_id
        pidx = step_row.parallel_index
        occurred = _claim_time(step_row, run)

        # A memoized step fired no effect this time. Its ledger row belongs to
        # whichever run first committed it, and the cassette variant of the same
        # replay leaves no ledger row at all - so checking it would flag every
        # bundle verification in the system.
        if step_row.replayed:
            report.replayed_skipped += 1
            continue

        # Evidence may still be in flight for a claim this fresh.
        if occurred > lag_cutoff:
            report.within_lag_window += 1
            continue

        step_def = step_defs.get(step_id) if step_defs is not None else None
        if step_defs is not None and step_def is None:
            # The YAML on disk has drifted since the run. We do not know what
            # this step was, and not knowing is not evidence.
            report.unresolved_steps += 1

        notify_claim = is_notify_delivery_claim(output)
        accept_claim = is_accept_approval_claim(output)

        # -- Pairs 1 and 2: the effect ledger --------------------------------
        #
        # One check, two finding types: the notify output shape proves the step
        # type on its own, and the definition (when resolved) decides the rest.
        # An explicit ``replay: live`` on the definition wins even over the
        # notify shape - the executor would not have claimed a row, so the sweep
        # must not expect one.
        if disclaims_side_effect(output):
            # The step says it changed nothing. There is no claim to back.
            expects_ledger = False
        elif step_def is not None:
            expects_ledger = _expects_ledger_row(step_def)
        else:
            expects_ledger = notify_claim

        if expects_ledger and report.ledger_enabled:
            if ttl_cutoff is not None and occurred < ttl_cutoff:
                # The row is gone because it expired, not because it never
                # existed. Absence proves nothing this far back.
                report.beyond_effect_ttl += 1
            else:
                expected_ledger_rows += 1
                outcome, found_status = _match_ledger(
                    ledger_by_step.get(step_id, []), pidx
                )
                if outcome == _MATCH_LOOSE:
                    report.suppressed_loose_match += 1
                elif outcome in (_MATCH_MISSING, _MATCH_UNSETTLED):
                    ledger_pending.append(
                        _ledger_finding(
                            run_id=run_id,
                            run=run,
                            step_row=step_row,
                            outcome=outcome,
                            found_status=found_status,
                            notify_claim=notify_claim,
                            step_def=step_def,
                            occurred=occurred,
                        )
                    )

        # -- Pair 3: the accept verdict on the audit chain --------------------
        if accept_claim and step_id not in accept_step_ids:
            findings.append(
                SilentSuccessFinding(
                    run_id=run_id,
                    workflow_name=run.workflow_name,
                    step_id=step_id,
                    parallel_index=pidx,
                    finding_type="accept_verdict_not_on_chain",
                    claim=(
                        "accept step recorded an evidence pack with "
                        "decision=approved"
                    ),
                    expected_evidence=(
                        "a 'step.accept' audit event for this run naming "
                        f"step '{step_id}'"
                    ),
                    found="no matching audit event on the run's chain",
                    severity="medium",
                    detail=(
                        "the verdict is unattributable: the pack exists in the "
                        "step output but the independent audit record of it "
                        "does not - this is a claim without evidence, not a "
                        "failed judgement"
                    ),
                    occurred_at=occurred,
                )
            )

        # -- Pair 4: a side-effecting completion on the audit chain -----------
        if (
            step_def is not None
            and getattr(step_def, "type", "") in CHAIN_CLAIM_STEP_TYPES
            and step_id not in chain_step_ids
        ):
            findings.append(
                SilentSuccessFinding(
                    run_id=run_id,
                    workflow_name=run.workflow_name,
                    step_id=step_id,
                    parallel_index=pidx,
                    finding_type="step_completed_not_on_chain",
                    claim=(
                        f"side-effecting step (type '{step_def.type}') is "
                        "recorded COMPLETED"
                    ),
                    expected_evidence=(
                        "a 'step.completed' audit event for this run naming "
                        f"step '{step_id}'"
                    ),
                    found="no matching audit event on the run's chain",
                    severity="medium",
                    detail=(
                        "the step row claims completion but the audit chain "
                        "has no record of it running"
                    ),
                    occurred_at=occurred,
                )
            )

    # A run with no committed ledger rows at all, while two or more of its steps
    # expected one, looks far more like an unreachable ledger (which executes
    # live and records nothing) than like every side effect in the run being
    # silent. Still reported - that is a real operational problem - but not at
    # the severity that would drown a report.
    committed_anywhere = any(
        row.status == _LEDGER_COMMITTED
        for rows in ledger_by_step.values()
        for row in rows
    )
    zero_coverage = expected_ledger_rows >= 2 and not committed_anywhere
    for finding in ledger_pending:
        if zero_coverage:
            findings.append(
                dataclasses.replace(
                    finding,
                    severity=_downgrade(finding.severity),
                    detail=(
                        f"{finding.detail}; ledger_coverage=none - this run "
                        "committed no effects at all, which more likely means "
                        "the ledger was unreachable while it ran than that "
                        "every effect was silent"
                    ),
                )
            )
        else:
            findings.append(finding)

    return findings


def _ledger_finding(
    *,
    run_id: str,
    run: Any,
    step_row: Any,
    outcome: str,
    found_status: str,
    notify_claim: bool,
    step_def: Any,
    occurred: datetime,
) -> SilentSuccessFinding:
    """Build the finding for a ledger claim that has no committed evidence."""
    step_type = getattr(step_def, "type", "") if step_def is not None else "notify"
    if notify_claim:
        finding_type = "notify_not_in_ledger"
        claim = (
            "notify step output claims delivery "
            f"(status=delivered, service={step_row.output_data.get('service')!r})"
        )
    else:
        finding_type = "effect_missing_from_ledger"
        claim = (
            f"side-effecting step (type '{step_type}') is recorded COMPLETED, "
            "which asserts its effect landed"
        )

    if outcome == _MATCH_UNSETTLED:
        finding_type = "effect_unsettled"
        found = f"a ledger row exists but its status is '{found_status}'"
        detail = (
            "the effect was claimed and never settled as committed while the "
            "step row reports success - the two records disagree"
        )
    else:
        found = "no committed row in run_step_effects for this effect scope"
        detail = (
            "the side effect this step reports has no durable record; the "
            "claim lacks evidence, which is not the same as the step having "
            "failed"
        )

    return SilentSuccessFinding(
        run_id=run_id,
        workflow_name=run.workflow_name,
        step_id=step_row.step_id,
        parallel_index=step_row.parallel_index,
        finding_type=finding_type,
        claim=claim,
        expected_evidence=(
            "a committed run_step_effects row in scope "
            f"{str(run.effect_scope_id or run.id)[:12]}... for step "
            f"'{step_row.step_id}'"
        ),
        found=found,
        severity="high",
        detail=detail,
        occurred_at=occurred,
    )


async def sweep_runs(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    tenant_id: str | None = None,
    run_ids: list[uuid.UUID] | None = None,
    limit: int = _DEFAULT_LIMIT,
    lag_hours: float | None = None,
) -> SweepReport:
    """Cross-check completed runs in a window and report unbacked claims.

    Only ``COMPLETED`` runs are swept: a failed or partial run is not claiming
    success, and its inconsistencies are already visible in its status.

    Four queries regardless of window size - runs, their step rows, the ledger
    rows for their effect scopes, and the two audit event types - then all the
    matching happens in Python. JSON payload matching in particular is done here
    rather than in SQL so the sweep behaves identically on SQLite and Postgres.
    """
    from sqlalchemy import select

    from sandcastle.config import settings
    from sandcastle.models.db import (
        AuditEvent,
        Run,
        RunStatus,
        RunStep,
        StepEffect,
        StepStatus,
        async_session,
    )

    if lag_hours is None:
        lag_hours = float(getattr(settings, "silent_success_lag_hours", 1.0) or 0.0)
    lag_hours = max(float(lag_hours), 0.0)

    now = datetime.now(timezone.utc)
    lag_cutoff = now - timedelta(hours=lag_hours)

    ledger_enabled = bool(getattr(settings, "effect_ledger_enabled", True))
    ttl_days = int(getattr(settings, "effect_ledger_ttl_days", 30) or 0)
    ttl_cutoff = now - timedelta(days=ttl_days) if ttl_days > 0 else None

    report = SweepReport(
        ledger_enabled=ledger_enabled,
        since=since,
        lag_hours=lag_hours,
    )

    async with async_session() as session:
        run_stmt = select(Run).where(Run.status == RunStatus.COMPLETED)
        if run_ids is not None:
            if not run_ids:
                return report
            run_stmt = run_stmt.where(Run.id.in_(run_ids))
        if since is not None:
            run_stmt = run_stmt.where(Run.created_at >= since)
        if until is not None:
            run_stmt = run_stmt.where(Run.created_at <= until)
        if tenant_id is not None:
            run_stmt = run_stmt.where(Run.tenant_id == tenant_id)
        run_stmt = run_stmt.order_by(Run.created_at.desc()).limit(limit)
        runs = list((await session.execute(run_stmt)).scalars().all())

        if not runs:
            return report

        run_uuids = [r.id for r in runs]
        # A replay inherits its parent's scope, so the committed row that backs
        # its steps lives under the parent id. Querying by run_id would read
        # every replay in the system as silent.
        scope_ids = {str(r.effect_scope_id or r.id) for r in runs}

        step_rows = list(
            (
                await session.execute(
                    select(RunStep).where(
                        RunStep.run_id.in_(run_uuids),
                        RunStep.status == StepStatus.COMPLETED,
                    )
                )
            )
            .scalars()
            .all()
        )
        steps_by_run: dict[uuid.UUID, list[Any]] = {}
        for row in step_rows:
            steps_by_run.setdefault(row.run_id, []).append(row)

        ledger_by_scope: dict[str, dict[str, list[Any]]] = {}
        if ledger_enabled and scope_ids:
            effect_rows = list(
                (
                    await session.execute(
                        select(StepEffect).where(
                            StepEffect.effect_scope_id.in_(sorted(scope_ids))
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in effect_rows:
                ledger_by_scope.setdefault(row.effect_scope_id, {}).setdefault(
                    row.step_id, []
                ).append(row)

        chain_by_run: dict[str, set[str]] = {}
        accept_by_run: dict[str, set[str]] = {}
        audit_rows = list(
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.run_id.in_(run_uuids),
                        AuditEvent.event_type.in_(("step.completed", "step.accept")),
                    )
                )
            )
            .scalars()
            .all()
        )
        for ev in audit_rows:
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            step_id = payload.get("step_id")
            if not isinstance(step_id, str):
                continue
            bucket = (
                accept_by_run if ev.event_type == "step.accept" else chain_by_run
            )
            bucket.setdefault(str(ev.run_id), set()).add(step_id)

    for run in runs:
        report.runs_checked += 1
        rows = steps_by_run.get(run.id, [])
        if not rows:
            continue
        step_defs = await _resolve_step_definitions(run)
        if step_defs is None:
            report.definition_unresolved.append(str(run.id))
        scope = str(run.effect_scope_id or run.id)
        report.findings.extend(
            _findings_for_run(
                run,
                rows,
                ledger_by_scope.get(scope, {}),
                chain_by_run.get(str(run.id), set()),
                accept_by_run.get(str(run.id), set()),
                step_defs,
                report,
                lag_cutoff=lag_cutoff,
                ttl_cutoff=ttl_cutoff,
            )
        )

    report.findings.sort(
        key=lambda f: (
            -SEVERITY_ORDER.index(f.severity)
            if f.severity in SEVERITY_ORDER
            else 0,
            f.run_id,
            f.step_id,
        )
    )
    return report


async def sweep_run(run_id: str, *, lag_hours: float | None = None) -> SweepReport:
    """Cross-check a single run. Unknown or non-completed run -> empty report."""
    try:
        run_uuid = uuid.UUID(str(run_id))
    except ValueError:
        return SweepReport(lag_hours=lag_hours or 0.0)
    return await sweep_runs(run_ids=[run_uuid], lag_hours=lag_hours, limit=1)
