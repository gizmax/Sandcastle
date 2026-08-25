"""Self-Healing Workflows engine.

Closes the loop on failed runs: scan unresolved dead-letter items, ask the
advisor LLM for a minimal patched workflow YAML plus a diagnosis and a
confidence score, validate the patch through the DAG parser, and file it as a
new draft workflow version behind an ApprovalRequest. With
``healer_auto_apply=true`` and confidence above the configured threshold the
patch is published directly and the approval is recorded as auto-approved.

The resolution loop runs at the start of every healer pass: applied patches
whose workflow has since completed a successful run mark the originating
DeadLetterItem as resolved (``resolved_by="healer"``); a failed run after the
patch marks the attempt as regressed so the item can be healed again, up to
``healer_max_attempts`` times.
"""

from __future__ import annotations

import difflib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from sandcastle.config import settings

logger = logging.getLogger(__name__)

# Statuses of attempts that are still in flight (a new heal would be redundant).
_ACTIVE_STATUSES = ("proposed", "applied", "auto_applied")
# Statuses that count against healer_max_attempts.
_COUNTED_STATUSES = ("proposed", "applied", "auto_applied", "rejected", "regressed")

_SYSTEM_PROMPT = (
    "You are a workflow repair expert for the Sandcastle orchestrator. "
    "You receive a workflow YAML, the failing step, and the error from the dead "
    "letter queue. Produce the smallest possible change to the YAML that fixes "
    "the failure while preserving the workflow's intent and structure. "
    "Respond with ONLY a JSON object with exactly these keys: "
    '"diagnosis" (one paragraph explaining the root cause and the fix), '
    '"confidence" (a number between 0 and 1), '
    '"patched_yaml" (the complete patched workflow YAML as a string). '
    "Do not wrap the JSON in markdown fences."
)


async def _call_llm(system: str, user: str) -> str:
    """Call the advisor LLM (same provider failover stack as generation/evolution)."""
    from sandcastle.engine.generator import _call_advisor_llm

    return await _call_advisor_llm(system=system, user=user, max_tokens=4096, purpose="evolution")


def _parse_llm_response(raw: str) -> tuple[str, float, str]:
    """Parse the LLM response into (diagnosis, confidence, patched_yaml).

    Tolerates markdown code fences around the JSON object.

    Raises:
        ValueError: when the response is not valid JSON or misses required keys.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"healer LLM response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("healer LLM response is not a JSON object")
    diagnosis = str(data.get("diagnosis", "")).strip()
    patched_yaml = data.get("patched_yaml", "")
    if not diagnosis or not isinstance(patched_yaml, str) or not patched_yaml.strip():
        raise ValueError("healer LLM response misses 'diagnosis' or 'patched_yaml'")
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return diagnosis, confidence, patched_yaml


def _make_diff(old_yaml: str, new_yaml: str, workflow_name: str) -> str:
    """Build a unified diff between the current and the patched workflow YAML."""
    return "".join(
        difflib.unified_diff(
            old_yaml.splitlines(keepends=True),
            new_yaml.splitlines(keepends=True),
            fromfile=f"{workflow_name}.yaml",
            tofile=f"{workflow_name}.yaml (healed)",
        )
    )


def _load_yaml_from_disk(workflow_name: str) -> str | None:
    """Load workflow YAML from the workflows directory, or None if missing."""
    workflows_dir = Path(settings.workflows_dir).resolve()
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in workflow_name)
    candidate = (workflows_dir / f"{safe_name}.yaml").resolve()
    if candidate.is_relative_to(workflows_dir) and candidate.is_file():
        return candidate.read_text()
    return None


async def _load_current_yaml(workflow_name: str) -> tuple[str, int | None] | None:
    """Load the current workflow YAML, preferring the registry over disk.

    Returns (yaml_content, version) where version is None for disk-only
    workflows, or None when the workflow cannot be found at all.
    """
    from sandcastle.models.db import WorkflowVersion, WorkflowVersionStatus, async_session

    async with async_session() as session:
        stmt = (
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_name == workflow_name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
            )
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
        wv = (await session.execute(stmt)).scalar_one_or_none()
        if wv is None:
            stmt = (
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_name == workflow_name)
                .order_by(WorkflowVersion.version.desc())
                .limit(1)
            )
            wv = (await session.execute(stmt)).scalar_one_or_none()
        if wv is not None:
            return wv.yaml_content, wv.version
    yaml_content = _load_yaml_from_disk(workflow_name)
    if yaml_content is not None:
        return yaml_content, None
    return None


async def _last_successful_run_summary(workflow_name: str) -> str:
    """Describe the last successful run of the workflow for LLM context."""
    from sandcastle.models.db import Run, RunStatus, async_session

    async with async_session() as session:
        stmt = (
            select(Run)
            .where(Run.workflow_name == workflow_name, Run.status == RunStatus.COMPLETED)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        return "No successful run of this workflow on record."
    version = f" (workflow version {run.workflow_version})" if run.workflow_version else ""
    return (
        f"Last successful run{version} completed at {run.completed_at or run.created_at} "
        f"with input: {json.dumps(run.input_data, default=str)[:500]}"
    )


def _build_user_prompt(
    workflow_name: str,
    workflow_yaml: str,
    step_id: str,
    error: str,
    attempts: int,
    last_success: str,
) -> str:
    """Assemble the user prompt with all failure context."""
    return (
        f"Workflow '{workflow_name}' failed at step '{step_id}' after {attempts} attempt(s).\n\n"
        f"Error:\n{error[:2000]}\n\n"
        f"{last_success}\n\n"
        f"Current workflow YAML:\n{workflow_yaml}\n\n"
        "Produce the minimal patch that fixes this failure."
    )


async def _count_attempts(session, dead_letter_id: uuid.UUID) -> int:
    """Count heal attempts already made for a dead-letter item."""
    from sandcastle.models.db import HealAttempt

    return (
        await session.scalar(
            select(func.count(HealAttempt.id)).where(
                HealAttempt.dead_letter_id == dead_letter_id,
                HealAttempt.status.in_(_COUNTED_STATUSES),
            )
        )
    ) or 0


async def _has_active_attempt(session, dead_letter_id: uuid.UUID) -> bool:
    """Check whether the item already has an in-flight heal attempt."""
    from sandcastle.models.db import HealAttempt

    result = await session.scalar(
        select(HealAttempt.id)
        .where(
            HealAttempt.dead_letter_id == dead_letter_id,
            HealAttempt.status.in_(_ACTIVE_STATUSES),
        )
        .limit(1)
    )
    return result is not None


async def _publish_version(workflow_name: str, version: int) -> None:
    """Publish a workflow version: archive current production, promote, sync disk."""
    from sandcastle.models.db import WorkflowVersion, WorkflowVersionStatus, async_session

    async with async_session() as session:
        prod_stmt = select(WorkflowVersion).where(
            WorkflowVersion.workflow_name == workflow_name,
            WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
        )
        for old_prod in (await session.execute(prod_stmt)).scalars().all():
            old_prod.status = WorkflowVersionStatus.ARCHIVED
        stmt = select(WorkflowVersion).where(
            WorkflowVersion.workflow_name == workflow_name,
            WorkflowVersion.version == version,
        )
        wv = (await session.execute(stmt)).scalar_one_or_none()
        if wv is None:
            raise ValueError(f"workflow version {workflow_name} v{version} not found")
        wv.status = WorkflowVersionStatus.PRODUCTION
        wv.promoted_by = "healer"
        wv.promoted_at = datetime.now(timezone.utc)
        yaml_content = wv.yaml_content
        await session.commit()

    # Keep the disk copy in sync for backward compatibility
    try:
        workflows_dir = Path(settings.workflows_dir)
        workflows_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in workflow_name)
        (workflows_dir / f"{safe_name}.yaml").write_text(yaml_content)
    except OSError:
        logger.warning("Could not write healed workflow %s to disk", workflow_name, exc_info=True)


async def _emit_audit(event_type: str, run_id: str | None, payload: dict) -> None:
    """Append an audit event in its own session; failures never abort healing."""
    try:
        from sandcastle.engine.audit import append_audit_event
        from sandcastle.models.db import async_session

        async with async_session() as session:
            await append_audit_event(
                session=session,
                event_type=event_type,
                run_id=run_id,
                actor_id="healer",
                payload=payload,
            )
            # append_audit_event only stages the row; without an explicit
            # commit the context-manager exit rolls it back and every healer
            # event silently vanishes from the audit chain.
            await session.commit()
    except Exception as exc:
        logger.debug("Audit event %s failed: %s", event_type, exc)


async def heal_item(item_id: uuid.UUID) -> dict:
    """Run one heal cycle for a single dead-letter item.

    Returns a result dict with at least a "status" key:
    proposed | auto_applied | rejected | failed | skipped.
    """
    from sandcastle.engine.dag import parse_yaml_string
    from sandcastle.models.db import (
        ApprovalRequest,
        ApprovalStatus,
        DeadLetterItem,
        HealAttempt,
        Run,
        WorkflowVersion,
        WorkflowVersionStatus,
        async_session,
    )

    async with async_session() as session:
        item = await session.get(DeadLetterItem, item_id)
        if item is None or item.resolved_at is not None:
            return {"status": "skipped", "reason": "item missing or already resolved"}
        run = await session.get(Run, item.run_id)
        if run is None:
            return {"status": "skipped", "reason": "originating run not found"}
        workflow_name = run.workflow_name
        if await _has_active_attempt(session, item_id):
            return {"status": "skipped", "reason": "heal already in flight"}
        if await _count_attempts(session, item_id) >= settings.healer_max_attempts:
            return {"status": "skipped", "reason": "max heal attempts reached"}
        # Capture primitives before the session closes (ORM object detaches)
        step_id: str = item.step_id
        error_text: str = item.error or "(no error recorded)"
        prior_attempts: int = item.attempts
        run_id = item.run_id

    loaded = await _load_current_yaml(workflow_name)
    if loaded is None:
        return {"status": "skipped", "reason": f"workflow '{workflow_name}' not found"}
    current_yaml, current_version = loaded
    last_success = await _last_successful_run_summary(workflow_name)

    user_prompt = _build_user_prompt(
        workflow_name=workflow_name,
        workflow_yaml=current_yaml,
        step_id=step_id,
        error=error_text,
        attempts=prior_attempts,
        last_success=last_success,
    )

    try:
        raw = await _call_llm(_SYSTEM_PROMPT, user_prompt)
        diagnosis, confidence, patched_yaml = _parse_llm_response(raw)
    except Exception as exc:
        logger.warning("Healer LLM call failed for %s/%s: %s", workflow_name, step_id, exc)
        async with async_session() as session:
            session.add(
                HealAttempt(
                    dead_letter_id=item_id,
                    workflow_name=workflow_name,
                    step_id=step_id,
                    diagnosis=f"LLM call failed: {exc}",
                    status="failed",
                )
            )
            await session.commit()
        return {"status": "failed", "reason": str(exc)}

    # Validate the patch through the existing DAG parser
    try:
        patched_workflow = parse_yaml_string(patched_yaml)
    except Exception as exc:
        logger.info("Healer patch for %s rejected (unparseable): %s", workflow_name, exc)
        async with async_session() as session:
            session.add(
                HealAttempt(
                    dead_letter_id=item_id,
                    workflow_name=workflow_name,
                    step_id=step_id,
                    diagnosis=diagnosis,
                    confidence=confidence,
                    status="rejected",
                )
            )
            await session.commit()
        return {"status": "rejected", "reason": f"patch does not parse: {exc}"}

    diff = _make_diff(current_yaml, patched_yaml, workflow_name)
    auto_apply = settings.healer_auto_apply and confidence >= settings.healer_confidence_threshold
    now = datetime.now(timezone.utc)

    import hashlib

    async with async_session() as session:
        next_version = (
            await session.scalar(
                select(func.max(WorkflowVersion.version)).where(
                    WorkflowVersion.workflow_name == workflow_name
                )
            )
            or 0
        ) + 1
        wv = WorkflowVersion(
            workflow_name=workflow_name,
            version=next_version,
            status=WorkflowVersionStatus.DRAFT,
            yaml_content=patched_yaml,
            description=f"Healer patch for step '{step_id}': {diagnosis[:400]}",
            steps_count=len(patched_workflow.steps),
            checksum=hashlib.sha256(patched_yaml.encode()).hexdigest(),
            created_by="healer",
        )
        session.add(wv)

        approval = ApprovalRequest(
            run_id=run_id,
            step_id=step_id,
            status=ApprovalStatus.APPROVED if auto_apply else ApprovalStatus.PENDING,
            message=(
                f"[Self-Healing] Patch proposed for workflow '{workflow_name}' "
                f"(v{current_version or '?'} -> v{next_version}).\n\n"
                f"Diagnosis: {diagnosis}\n\nConfidence: {confidence:.2f}\n\nDiff:\n{diff}"
            ),
            request_data={
                "type": "healer",
                "workflow_name": workflow_name,
                "from_version": current_version,
                "to_version": next_version,
                "confidence": confidence,
                "dead_letter_id": str(item_id),
            },
            reviewer_id="healer" if auto_apply else None,
            reviewer_comment=(
                f"Auto-approved: confidence {confidence:.2f} >= "
                f"threshold {settings.healer_confidence_threshold:.2f}"
                if auto_apply
                else None
            ),
            resolved_at=now if auto_apply else None,
        )
        session.add(approval)
        await session.flush()

        attempt = HealAttempt(
            dead_letter_id=item_id,
            workflow_name=workflow_name,
            step_id=step_id,
            diagnosis=diagnosis,
            confidence=confidence,
            diff=diff,
            from_version=current_version,
            to_version=next_version,
            status="auto_applied" if auto_apply else "proposed",
            approval_id=approval.id,
            applied_at=now if auto_apply else None,
        )
        session.add(attempt)
        await session.commit()
        attempt_id = attempt.id

    if auto_apply:
        await _publish_version(workflow_name, next_version)

    await _emit_audit(
        "healer.patch_auto_applied" if auto_apply else "healer.patch_proposed",
        run_id=str(run_id),
        payload={
            "workflow_name": workflow_name,
            "step_id": step_id,
            "to_version": next_version,
            "confidence": confidence,
            "heal_attempt_id": str(attempt_id),
        },
    )
    return {
        "status": "auto_applied" if auto_apply else "proposed",
        "workflow_name": workflow_name,
        "to_version": next_version,
        "confidence": confidence,
        "heal_attempt_id": str(attempt_id),
    }


async def apply_approved_heals() -> int:
    """Publish patches whose approval request has been approved by a human.

    Returns the number of patches published.
    """
    from sandcastle.models.db import ApprovalRequest, ApprovalStatus, HealAttempt, async_session

    applied = 0
    async with async_session() as session:
        stmt = (
            select(HealAttempt, ApprovalRequest)
            .join(ApprovalRequest, HealAttempt.approval_id == ApprovalRequest.id)
            .where(
                HealAttempt.status == "proposed",
                ApprovalRequest.status == ApprovalStatus.APPROVED,
            )
        )
        rows = (await session.execute(stmt)).all()
    for attempt, _approval in rows:
        try:
            await _publish_version(attempt.workflow_name, attempt.to_version)
        except Exception as exc:
            logger.warning("Could not publish heal %s: %s", attempt.id, exc)
            continue
        async with async_session() as session:
            db_attempt = await session.get(HealAttempt, attempt.id)
            if db_attempt is not None:
                db_attempt.status = "applied"
                db_attempt.applied_at = datetime.now(timezone.utc)
                await session.commit()
        applied += 1
        await _emit_audit(
            "healer.patch_applied",
            run_id=None,
            payload={
                "workflow_name": attempt.workflow_name,
                "to_version": attempt.to_version,
                "heal_attempt_id": str(attempt.id),
            },
        )
    return applied


async def check_heal_resolutions() -> dict:
    """Resolve or regress applied heals based on the workflow's runs since the patch.

    A successful run after the patch marks the originating DeadLetterItem
    resolved with resolved_by="healer" and the attempt as "succeeded". A failed
    run marks the attempt as "regressed" so the item stays open for another
    heal (bounded by healer_max_attempts).
    """
    from sandcastle.models.db import (
        DeadLetterItem,
        HealAttempt,
        Run,
        RunStatus,
        async_session,
    )

    resolved = 0
    regressed = 0
    async with async_session() as session:
        stmt = select(HealAttempt).where(
            HealAttempt.status.in_(("applied", "auto_applied")),
            HealAttempt.applied_at.is_not(None),
        )
        attempts = (await session.execute(stmt)).scalars().all()

        for attempt in attempts:
            run_stmt = (
                select(Run)
                .where(
                    Run.workflow_name == attempt.workflow_name,
                    Run.created_at > attempt.applied_at,
                    Run.status.in_((RunStatus.COMPLETED, RunStatus.FAILED)),
                )
                .order_by(Run.created_at.desc())
                .limit(1)
            )
            latest = (await session.execute(run_stmt)).scalar_one_or_none()
            if latest is None:
                continue  # no run since the patch yet
            if latest.status == RunStatus.COMPLETED:
                attempt.status = "succeeded"
                item = await session.get(DeadLetterItem, attempt.dead_letter_id)
                if item is not None and item.resolved_at is None:
                    item.resolved_at = datetime.now(timezone.utc)
                    item.resolved_by = "healer"
                resolved += 1
            else:
                attempt.status = "regressed"
                regressed += 1
        await session.commit()

    if resolved or regressed:
        await _emit_audit(
            "healer.resolutions_checked",
            run_id=None,
            payload={"resolved": resolved, "regressed": regressed},
        )
    return {"resolved": resolved, "regressed": regressed}


async def run_healer_pass(lookback_hours: int | None = None) -> dict:
    """Run a full healer pass: resolutions, approved publishes, then new heals.

    Args:
        lookback_hours: how far back to scan for unresolved dead-letter items;
            defaults to settings.healer_lookback_hours.

    Returns:
        Summary dict with scanned/proposed/auto_applied/rejected/failed/skipped
        counts plus the resolution-loop results.
    """
    from sandcastle.models.db import DeadLetterItem, async_session

    lookback = lookback_hours if lookback_hours is not None else settings.healer_lookback_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    resolutions = await check_heal_resolutions()
    published = await apply_approved_heals()

    async with async_session() as session:
        stmt = (
            select(DeadLetterItem.id)
            .where(
                DeadLetterItem.resolved_at.is_(None),
                DeadLetterItem.created_at >= cutoff,
            )
            .order_by(DeadLetterItem.created_at.asc())
        )
        item_ids = [row[0] for row in (await session.execute(stmt)).all()]

    summary = {
        "scanned": len(item_ids),
        "proposed": 0,
        "auto_applied": 0,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
        "published_approved": published,
        "resolved": resolutions["resolved"],
        "regressed": resolutions["regressed"],
    }
    for item_id in item_ids:
        try:
            result = await heal_item(item_id)
        except Exception as exc:
            logger.error("Healer crashed on item %s: %s", item_id, exc, exc_info=True)
            summary["failed"] += 1
            continue
        status = result.get("status", "failed")
        summary[status] = summary.get(status, 0) + 1

    logger.info("Healer pass complete: %s", summary)
    return summary
