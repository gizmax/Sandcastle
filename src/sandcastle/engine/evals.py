"""Eval gates - block workflow promotion when scored runs fall below threshold.

This module sits alongside the existing `sandcastle.engine.eval` suite runner.
It replays a curated GoldenDataset against a workflow and produces an
aggregate score (0-1). The gate is consulted before promoting / publishing a
workflow version so regressions never reach production.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from sandcastle.models.db import GoldenCase, GoldenDataset, async_session

logger = logging.getLogger(__name__)


# Default minimum aggregate score required to clear a gate.
DEFAULT_GATE_THRESHOLD = 0.7


@dataclass
class GoldenCaseResult:
    """Per-case outcome of a golden replay."""

    case_label: str
    passed: bool
    score: float
    expected_score_min: float
    duration_seconds: float = 0.0
    error: str | None = None
    output_summary: str | None = None


@dataclass
class EvalGateResult:
    """Aggregate outcome of replaying a golden dataset."""

    workflow_name: str
    version: int | None
    dataset_id: str
    dataset_name: str
    total_cases: int = 0
    passed_cases: int = 0
    aggregate_score: float = 0.0
    threshold: float = DEFAULT_GATE_THRESHOLD
    passed: bool = False
    cases: list[GoldenCaseResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Output scoring
# ---------------------------------------------------------------------------


def _score_against_expected(actual: Any, expected: Any) -> float:
    """Return a 0-1 similarity score between actual and expected output.

    Strategy:
    - If both are dicts: ratio of keys whose values are equal to expected.
    - Otherwise: 1.0 if equal, else fuzzy substring match for strings.
    """
    if expected is None:
        # Nothing to compare against - any non-empty output passes.
        return 1.0 if actual not in (None, "", {}, []) else 0.0

    if isinstance(expected, dict) and isinstance(actual, dict):
        if not expected:
            return 1.0
        matches = 0
        for key, exp_val in expected.items():
            if key in actual and actual[key] == exp_val:
                matches += 1
        return matches / len(expected)

    if isinstance(expected, str) and isinstance(actual, str):
        if actual == expected:
            return 1.0
        # Substring match as a lenient fallback.
        if expected and expected in actual:
            return 0.8
        return 0.0

    return 1.0 if actual == expected else 0.0


# ---------------------------------------------------------------------------
# Workflow execution wrapper (mockable in tests)
# ---------------------------------------------------------------------------


async def _execute_workflow_for_gate(
    workflow_name: str,
    version: int | None,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute the workflow and return its outputs dict.

    Isolated as a thin wrapper so tests can patch it without touching the
    real executor stack.
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import create_storage

    # Lazy import to avoid circular dependency with api.routes helpers.
    from sandcastle.api.routes import _load_versioned_workflow_yaml

    yaml_content = await _load_versioned_workflow_yaml(workflow_name, version)
    workflow = parse_yaml_string(yaml_content)
    plan = build_plan(workflow)
    storage = create_storage()
    result = await execute_workflow(
        workflow=workflow,
        plan=plan,
        input_data=input_data,
        run_id=str(uuid.uuid4()),
        storage=storage,
    )
    if hasattr(result, "outputs") and isinstance(result.outputs, dict):
        return result.outputs
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_against_golden(
    workflow_name: str,
    version: int | None,
    dataset_id: str | uuid.UUID,
    threshold: float = DEFAULT_GATE_THRESHOLD,
) -> EvalGateResult:
    """Replay all cases in a GoldenDataset against a workflow version.

    Returns an EvalGateResult containing per-case scores and aggregate score.
    Does not persist anything by itself - the caller decides whether to
    promote or reject.
    """
    if isinstance(dataset_id, str):
        try:
            dataset_uuid = uuid.UUID(dataset_id)
        except ValueError as exc:
            raise ValueError(f"Invalid dataset_id: {dataset_id}") from exc
    else:
        dataset_uuid = dataset_id

    async with async_session() as session:
        stmt = (
            select(GoldenDataset)
            .options(selectinload(GoldenDataset.cases))
            .where(GoldenDataset.id == dataset_uuid)
        )
        dataset = (await session.execute(stmt)).scalar_one_or_none()

    if dataset is None:
        raise LookupError(f"GoldenDataset {dataset_id} not found")

    result = EvalGateResult(
        workflow_name=workflow_name,
        version=version,
        dataset_id=str(dataset.id),
        dataset_name=dataset.name,
        threshold=threshold,
        cases=[],
    )

    if not dataset.cases:
        result.aggregate_score = 0.0
        result.passed = False
        return result

    scores: list[float] = []
    for case in dataset.cases:
        case_start = time.monotonic()
        try:
            outputs = await _execute_workflow_for_gate(
                workflow_name=workflow_name,
                version=version,
                input_data=case.input_data or {},
            )
            score = _score_against_expected(outputs, case.expected_output)
            case_passed = score >= case.expected_score_min
            result.cases.append(
                GoldenCaseResult(
                    case_label=case.case_label or str(case.id),
                    passed=case_passed,
                    score=score,
                    expected_score_min=case.expected_score_min,
                    duration_seconds=time.monotonic() - case_start,
                    output_summary=str(outputs)[:500] if outputs else None,
                )
            )
            scores.append(score)
        except Exception as exc:
            logger.warning(
                "Golden case '%s' failed during replay: %s", case.case_label, exc
            )
            result.cases.append(
                GoldenCaseResult(
                    case_label=case.case_label or str(case.id),
                    passed=False,
                    score=0.0,
                    expected_score_min=case.expected_score_min,
                    duration_seconds=time.monotonic() - case_start,
                    error=str(exc),
                )
            )
            scores.append(0.0)

    result.total_cases = len(result.cases)
    result.passed_cases = sum(1 for c in result.cases if c.passed)
    result.aggregate_score = sum(scores) / len(scores) if scores else 0.0
    result.passed = result.aggregate_score >= threshold
    return result


async def _find_active_dataset(
    workflow_name: str,
    tenant_id: str | None = None,
) -> GoldenDataset | None:
    """Return the most recent active GoldenDataset for the workflow, if any."""
    async with async_session() as session:
        stmt = (
            select(GoldenDataset)
            .where(
                GoldenDataset.workflow_name == workflow_name,
                GoldenDataset.is_active.is_(True),
            )
            .order_by(GoldenDataset.version.desc(), GoldenDataset.created_at.desc())
        )
        if tenant_id is not None:
            stmt = stmt.where(GoldenDataset.tenant_id == tenant_id)
        return (await session.execute(stmt)).scalars().first()


async def gate_promotion(
    workflow_name: str,
    target_version: int | None,
    min_score: float = DEFAULT_GATE_THRESHOLD,
    tenant_id: str | None = None,
) -> tuple[bool, EvalGateResult | None]:
    """Look up the active golden dataset and run the gate.

    Returns (passed, result). When no active dataset exists the gate is a
    no-op and returns (True, None) - this lets callers opt-in without forcing
    every workflow to have a dataset.
    """
    dataset = await _find_active_dataset(workflow_name, tenant_id=tenant_id)
    if dataset is None:
        return True, None

    result = await evaluate_against_golden(
        workflow_name=workflow_name,
        version=target_version,
        dataset_id=dataset.id,
        threshold=min_score,
    )
    return result.passed, result
