"""Evaluation & testing framework for Sandcastle workflows.

Define test suites in YAML, run them via CLI or API, and track results.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AssertionDef:
    """A single assertion to check against step/workflow output."""

    type: str
    value: Any = None
    criteria: str = ""
    threshold: float = 0.7
    step: str | None = None


@dataclass
class EvalCase:
    """A single test case within an eval suite."""

    name: str
    input: dict[str, Any]
    assertions: list[AssertionDef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalSuiteDef:
    """Parsed eval suite definition."""

    workflow: str
    cases: list[EvalCase]
    description: str = ""
    concurrency: int = 1


@dataclass
class AssertionResult:
    """Result of evaluating a single assertion."""

    type: str
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str = ""
    score: float | None = None


@dataclass
class CaseResult:
    """Result of running a single eval case."""

    name: str
    passed: bool
    assertions: list[AssertionResult] = field(default_factory=list)
    run_id: str | None = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    output: Any = None
    error: str | None = None


@dataclass
class SuiteResult:
    """Result of running an entire eval suite."""

    suite_name: str
    workflow: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    total_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    cases: list[CaseResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def parse_eval_suite(path: str | Path) -> EvalSuiteDef:
    """Parse an eval suite from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Eval suite not found: {path}")
    return parse_eval_suite_string(path.read_text())


def parse_eval_suite_string(content: str) -> EvalSuiteDef:
    """Parse an eval suite from a YAML string."""
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("Eval suite must be a YAML mapping")

    workflow = data.get("workflow")
    if not workflow:
        raise ValueError("Eval suite must specify a 'workflow' field")

    cases: list[EvalCase] = []
    for case_data in data.get("cases", []):
        assertions: list[AssertionDef] = []
        for a in case_data.get("assertions", []):
            assertions.append(
                AssertionDef(
                    type=a.get("type", "not_empty"),
                    value=a.get("value"),
                    criteria=a.get("criteria", ""),
                    threshold=float(a.get("threshold", 0.7)),
                    step=a.get("step"),
                )
            )
        cases.append(
            EvalCase(
                name=case_data.get("name", f"case_{len(cases) + 1}"),
                input=case_data.get("input", {}),
                assertions=assertions,
                tags=case_data.get("tags", []),
            )
        )

    return EvalSuiteDef(
        workflow=workflow,
        cases=cases,
        description=data.get("description", ""),
        concurrency=int(data.get("concurrency", 1)),
    )


# ---------------------------------------------------------------------------
# Assertion checking
# ---------------------------------------------------------------------------


async def check_assertion(
    assertion: AssertionDef,
    output: Any,
    run_metadata: dict[str, Any] | None = None,
    step_outputs: dict[str, Any] | None = None,
) -> AssertionResult:
    """Evaluate a single assertion against output.

    Args:
        assertion: The assertion definition.
        output: The workflow/step output to check.
        run_metadata: Dict with keys like cost_usd, duration_seconds.
        step_outputs: Dict mapping step_id -> output (for step-targeted assertions).
    """
    meta = run_metadata or {}
    target = output
    if assertion.step and step_outputs:
        target = step_outputs.get(assertion.step, output)

    atype = assertion.type

    if atype == "contains":
        actual = str(target) if target is not None else ""
        passed = assertion.value in actual
        return AssertionResult(
            type=atype,
            passed=passed,
            expected=assertion.value,
            actual=actual[:200] if len(actual) > 200 else actual,
            message="" if passed else f"Output does not contain '{assertion.value}'",
        )

    if atype == "not_contains":
        actual = str(target) if target is not None else ""
        passed = assertion.value not in actual
        return AssertionResult(
            type=atype,
            passed=passed,
            expected=f"not '{assertion.value}'",
            actual=actual[:200] if len(actual) > 200 else actual,
            message="" if passed else f"Output contains '{assertion.value}'",
        )

    if atype == "not_empty":
        passed = target is not None and target != "" and target != {}
        return AssertionResult(
            type=atype,
            passed=passed,
            expected="non-empty output",
            actual=type(target).__name__ if target is not None else "None",
            message="" if passed else "Output is empty or None",
        )

    if atype == "equals":
        passed = target == assertion.value
        return AssertionResult(
            type=atype,
            passed=passed,
            expected=assertion.value,
            actual=target,
            message="" if passed else f"Expected {assertion.value!r}, got {target!r}",
        )

    if atype == "regex_match":
        actual = str(target) if target is not None else ""
        if not assertion.value:
            match = None
        else:
            # Validate regex for safety (length and ReDoS risk)
            from sandcastle.engine.policy import (
                _MAX_REGEX_LENGTH,
                _has_redos_risk,
            )

            if len(assertion.value) > _MAX_REGEX_LENGTH:
                return AssertionResult(
                    type=atype,
                    passed=False,
                    expected=assertion.value[:80],
                    message=f"Regex pattern too long ({len(assertion.value)} chars, max {_MAX_REGEX_LENGTH})",
                )
            if _has_redos_risk(assertion.value):
                return AssertionResult(
                    type=atype,
                    passed=False,
                    expected=assertion.value[:80],
                    message="Regex pattern rejected: potential catastrophic backtracking (ReDoS)",
                )
            match = re.search(assertion.value, actual)
        passed = match is not None
        return AssertionResult(
            type=atype,
            passed=passed,
            expected=assertion.value,
            actual=actual[:200] if len(actual) > 200 else actual,
            message="" if passed else f"Output does not match pattern '{assertion.value}'",
        )

    if atype == "llm_judge":
        return await _check_llm_judge(assertion, target)

    if atype == "schema_match":
        return _check_schema_match(assertion, target)

    if atype == "max_cost":
        actual_cost = meta.get("cost_usd", 0.0)
        passed = actual_cost <= assertion.value
        return AssertionResult(
            type=atype,
            passed=passed,
            expected=f"<= ${assertion.value:.4f}",
            actual=f"${actual_cost:.4f}",
            message=""
            if passed
            else f"Cost ${actual_cost:.4f} exceeds limit ${assertion.value:.4f}",
        )

    if atype == "max_duration":
        actual_dur = meta.get("duration_seconds", 0.0)
        passed = actual_dur <= assertion.value
        return AssertionResult(
            type=atype,
            passed=passed,
            expected=f"<= {assertion.value}s",
            actual=f"{actual_dur:.1f}s",
            message=""
            if passed
            else f"Duration {actual_dur:.1f}s exceeds limit {assertion.value}s",
        )

    return AssertionResult(
        type=atype,
        passed=False,
        message=f"Unknown assertion type: {atype}",
    )


async def _check_llm_judge(assertion: AssertionDef, output: Any) -> AssertionResult:
    """Use LLM judge to evaluate output quality."""
    try:
        from sandcastle.engine.autopilot import _evaluate_llm_judge
        from sandcastle.engine.dag import AutoPilotConfig, EvaluationConfig

        eval_cfg = EvaluationConfig(
            method="llm_judge",
            criteria=assertion.criteria or "overall quality and correctness",
        )
        config = AutoPilotConfig(evaluation=eval_cfg)
        score = await _evaluate_llm_judge(output, config)
        passed = score >= assertion.threshold
        return AssertionResult(
            type="llm_judge",
            passed=passed,
            expected=f">= {assertion.threshold}",
            actual=f"{score:.2f}",
            score=score,
            message=""
            if passed
            else f"LLM judge score {score:.2f} below threshold {assertion.threshold}",
        )
    except Exception as exc:
        logger.warning("LLM judge failed: %s", exc)
        return AssertionResult(
            type="llm_judge",
            passed=False,
            expected=f">= {assertion.threshold}",
            message=f"LLM judge error: {exc}",
        )


def _check_schema_match(assertion: AssertionDef, output: Any) -> AssertionResult:
    """Check output against a schema for completeness."""
    from sandcastle.engine.autopilot import _evaluate_schema_completeness

    schema = assertion.value if isinstance(assertion.value, dict) else None
    threshold = assertion.threshold if assertion.threshold else 0.8
    score = _evaluate_schema_completeness(output, schema)
    passed = score >= threshold
    return AssertionResult(
        type="schema_match",
        passed=passed,
        expected=f">= {threshold}",
        actual=f"{score:.2f}",
        score=score,
        message="" if passed else f"Schema completeness {score:.2f} below threshold {threshold}",
    )


# ---------------------------------------------------------------------------
# Eval execution
# ---------------------------------------------------------------------------


async def run_eval_case(
    case: EvalCase,
    workflow_name: str,
    tenant_id: str | None = None,
) -> CaseResult:
    """Execute a workflow with the given input and check all assertions."""
    start_time = time.monotonic()
    run_id = str(uuid.uuid4())

    try:
        from sandcastle.engine.dag import build_plan, load_workflow
        from sandcastle.engine.executor import execute_workflow
        from sandcastle.engine.storage import create_storage

        workflow = load_workflow(workflow_name)
        plan = build_plan(workflow)
        storage = create_storage()

        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data=case.input,
            run_id=run_id,
            storage=storage,
            tenant_id=tenant_id,
        )

        duration = time.monotonic() - start_time
        cost = result.total_cost_usd

        # Collect step outputs for step-targeted assertions
        step_outputs: dict[str, Any] = {}
        if hasattr(result, "outputs") and isinstance(result.outputs, dict):
            step_outputs = result.outputs

        # Final output is the last step's output or the full outputs dict
        final_output = result.outputs

        # Check all assertions
        meta = {"cost_usd": cost, "duration_seconds": duration}
        assertion_results: list[AssertionResult] = []
        for assertion in case.assertions:
            ar = await check_assertion(assertion, final_output, meta, step_outputs)
            assertion_results.append(ar)

        all_passed = all(ar.passed for ar in assertion_results)
        error = result.error if hasattr(result, "error") else None

        return CaseResult(
            name=case.name,
            passed=all_passed and result.status == "completed",
            assertions=assertion_results,
            run_id=run_id,
            cost_usd=cost,
            duration_seconds=duration,
            output=_summarize_output(final_output),
            error=error,
        )
    except Exception as exc:
        duration = time.monotonic() - start_time
        logger.error("Eval case '%s' failed: %s", case.name, exc)
        return CaseResult(
            name=case.name,
            passed=False,
            run_id=run_id,
            duration_seconds=duration,
            error=str(exc),
        )


async def run_eval_suite(
    suite: EvalSuiteDef,
    tag_filter: list[str] | None = None,
    concurrency: int | None = None,
    tenant_id: str | None = None,
) -> SuiteResult:
    """Run all cases in an eval suite with concurrency control."""
    cases = suite.cases
    if tag_filter:
        tag_set = set(tag_filter)
        cases = [c for c in cases if tag_set.intersection(c.tags)]

    max_concurrent = concurrency or suite.concurrency or 1
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_with_semaphore(case: EvalCase) -> CaseResult:
        async with semaphore:
            if tenant_id is None:
                return await run_eval_case(case, suite.workflow)
            return await run_eval_case(case, suite.workflow, tenant_id=tenant_id)

    tasks = [asyncio.create_task(_run_with_semaphore(c)) for c in cases]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    case_results: list[CaseResult] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            case_results.append(
                CaseResult(
                    name=cases[i].name,
                    passed=False,
                    error=str(r),
                )
            )
        else:
            case_results.append(r)

    total = len(case_results)
    passed = sum(1 for c in case_results if c.passed)
    failed = total - passed
    total_cost = sum(c.cost_usd for c in case_results)
    total_duration = sum(c.duration_seconds for c in case_results)

    return SuiteResult(
        suite_name=suite.description or suite.workflow,
        workflow=suite.workflow,
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=passed / total if total > 0 else 0.0,
        total_cost_usd=total_cost,
        total_duration_seconds=total_duration,
        cases=case_results,
    )


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


async def save_eval_run(suite_result: SuiteResult, suite_yaml: str = "") -> str:
    """Persist eval run + case results to the database. Returns eval_run_id."""
    from sandcastle.models.db import EvalCaseResult, EvalRun, EvalRunStatus, async_session

    eval_run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    eval_run = EvalRun(
        id=eval_run_id,
        suite_name=suite_result.suite_name,
        workflow_name=suite_result.workflow,
        status=EvalRunStatus.COMPLETED,
        total_cases=suite_result.total,
        passed_cases=suite_result.passed,
        failed_cases=suite_result.failed,
        pass_rate=suite_result.pass_rate,
        total_cost_usd=suite_result.total_cost_usd,
        total_duration_seconds=suite_result.total_duration_seconds,
        suite_yaml=suite_yaml,
        started_at=now,
        completed_at=now,
    )

    case_rows = []
    for cr in suite_result.cases:
        assertion_data = [
            {
                "type": ar.type,
                "passed": ar.passed,
                "expected": ar.expected,
                "actual": ar.actual,
                "message": ar.message,
                "score": ar.score,
            }
            for ar in cr.assertions
        ]
        case_rows.append(
            EvalCaseResult(
                eval_run_id=eval_run_id,
                case_name=cr.name,
                passed=cr.passed,
                run_id=uuid.UUID(cr.run_id) if cr.run_id else None,
                cost_usd=cr.cost_usd,
                duration_seconds=cr.duration_seconds,
                assertions=assertion_data,
                output_summary=str(cr.output)[:500] if cr.output else None,
                error=cr.error,
            )
        )

    async with async_session() as session:
        session.add(eval_run)
        session.add_all(case_rows)
        await session.commit()

    return str(eval_run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_output(output: Any, max_len: int = 500) -> Any:
    """Truncate output for storage."""
    if output is None:
        return None
    if isinstance(output, str):
        return output[:max_len] if len(output) > max_len else output
    if isinstance(output, dict):
        s = str(output)
        if len(s) > max_len:
            return s[:max_len]
        return output
    return str(output)[:max_len]
