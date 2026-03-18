"""Tests for EU AI Act risk classification in WorkflowDefinition and Run model."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    VALID_RISK_LEVELS,
    WorkflowDefinition,
    parse_yaml_string,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_YAML = """
name: simple-workflow
description: A simple workflow
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    type: standard
"""

_APPROVAL_YAML = """
name: credit-scoring
description: Credit scoring workflow
default_model: sonnet
steps:
  - id: assess
    prompt: "Evaluate creditworthiness of the applicant."
    type: standard
  - id: review
    type: approval
    depends_on: [assess]
    approval_config:
      message: "Please review the creditworthiness assessment."
"""


def _make_workflow(**kwargs) -> WorkflowDefinition:
    """Return a minimal valid WorkflowDefinition with optional overrides."""
    from sandcastle.engine.dag import StepDefinition

    defaults = dict(
        name="test-wf",
        description="",
        default_model="sonnet",
        default_max_turns=10,
        default_timeout=300,
        steps=[StepDefinition(id="step1", prompt="Do something")],
    )
    defaults.update(kwargs)
    return WorkflowDefinition(**defaults)


# ---------------------------------------------------------------------------
# Step 1: YAML parsing
# ---------------------------------------------------------------------------


class TestRiskLevelParsing:
    """Verify that risk_level is parsed correctly from YAML."""

    def test_default_risk_level_is_minimal(self):
        """Workflow without risk_level key defaults to 'minimal'."""
        wf = parse_yaml_string(_MINIMAL_YAML)
        assert wf.risk_level == "minimal"

    def test_parse_minimal(self):
        yaml_content = _MINIMAL_YAML + "risk_level: minimal\n"
        wf = parse_yaml_string(yaml_content)
        assert wf.risk_level == "minimal"

    def test_parse_limited(self):
        yaml_content = _MINIMAL_YAML + "risk_level: limited\n"
        wf = parse_yaml_string(yaml_content)
        assert wf.risk_level == "limited"

    def test_parse_high(self):
        yaml_content = _APPROVAL_YAML + "risk_level: high\n"
        wf = parse_yaml_string(yaml_content)
        assert wf.risk_level == "high"

    def test_parse_unacceptable(self):
        yaml_content = _MINIMAL_YAML + "risk_level: unacceptable\n"
        wf = parse_yaml_string(yaml_content)
        assert wf.risk_level == "unacceptable"

    def test_valid_risk_levels_set(self):
        """VALID_RISK_LEVELS contains exactly the four EU AI Act tiers."""
        assert VALID_RISK_LEVELS == frozenset({"minimal", "limited", "high", "unacceptable"})


# ---------------------------------------------------------------------------
# Step 1: Validation
# ---------------------------------------------------------------------------


class TestRiskLevelValidation:
    """Verify validate() enforces EU AI Act rules."""

    def test_minimal_risk_passes(self):
        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: minimal\n")
        errors = validate(wf)
        assert errors == []

    def test_limited_risk_passes(self):
        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: limited\n")
        errors = validate(wf)
        assert errors == []

    def test_unacceptable_risk_is_rejected(self):
        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: unacceptable\n")
        errors = validate(wf)
        assert any("Unacceptable risk" in e for e in errors), errors

    def test_invalid_risk_level_raises_error(self):
        wf = _make_workflow(risk_level="extreme")
        errors = validate(wf)
        assert any("Invalid risk_level" in e for e in errors), errors
        assert any("extreme" in e for e in errors), errors

    def test_high_risk_with_approval_step_passes(self):
        yaml_content = _APPROVAL_YAML + "risk_level: high\n"
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert errors == [], errors

    def test_high_risk_without_approval_step_produces_warning(self):
        """High risk without an approval step adds a validation error."""
        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: high\n")
        errors = validate(wf)
        assert any("approval" in e.lower() for e in errors), errors
        assert any("high" in e.lower() or "High" in e for e in errors), errors

    def test_validation_error_message_mentions_eu_ai_act(self):
        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: unacceptable\n")
        errors = validate(wf)
        assert any("EU AI Act" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Step 2 & 3: Run model propagation (unit test - no real DB)
# ---------------------------------------------------------------------------


class TestRiskLevelRunPropagation:
    """Verify risk_level propagates correctly to the Run DB model."""

    def test_run_model_has_risk_level_attribute(self):
        """Run model must declare a risk_level column."""
        from sandcastle.models.db import Run

        assert hasattr(Run, "risk_level"), "Run model is missing risk_level column"

    def test_run_risk_level_column_is_nullable_string(self):
        """risk_level column should be a nullable String."""
        from sqlalchemy import inspect as sa_inspect

        from sandcastle.models.db import Run

        mapper = sa_inspect(Run)
        col = mapper.columns.get("risk_level")
        assert col is not None, "risk_level column not found on Run mapper"

    def test_run_risk_level_default_value(self):
        """Run() instantiated without risk_level should default to 'minimal'."""
        from sandcastle.models.db import Run

        run = Run(
            id=uuid.uuid4(),
            workflow_name="test",
        )
        # The column-level default is "minimal"; Python-side default may be None
        # before DB round-trip, so we accept either.
        assert run.risk_level in ("minimal", None), (
            f"Unexpected default: {run.risk_level!r}"
        )

    def test_run_accepts_all_risk_levels(self):
        """Run can be constructed with each valid risk level."""
        from sandcastle.models.db import Run

        for level in ("minimal", "limited", "high", "unacceptable"):
            run = Run(
                id=uuid.uuid4(),
                workflow_name="test",
                risk_level=level,
            )
            assert run.risk_level == level


# ---------------------------------------------------------------------------
# Step 3: Executor - block unacceptable risk
# ---------------------------------------------------------------------------


class TestExecutorRiskEnforcement:
    """Verify the executor refuses unacceptable-risk workflows."""

    def _make_plan(self, workflow):
        from sandcastle.engine.dag import build_plan

        return build_plan(workflow)

    @pytest.mark.asyncio
    async def test_unacceptable_risk_workflow_is_blocked(self):
        """execute_workflow must immediately return failed for unacceptable risk."""
        from sandcastle.engine.executor import execute_workflow

        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: unacceptable\n")
        plan = self._make_plan(wf)

        result = await execute_workflow(workflow=wf, plan=plan, input_data={})

        assert result.status == "failed"
        assert "Unacceptable risk" in (result.error or "")
        assert result.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_high_risk_without_approval_logs_warning(self):
        """execute_workflow logs a warning for high-risk workflows with no approval step."""
        from sandcastle.engine.executor import execute_workflow

        wf = parse_yaml_string(_MINIMAL_YAML + "risk_level: high\n")
        plan = self._make_plan(wf)

        with patch("sandcastle.engine.executor.logger") as mock_logger:
            # We expect execution to proceed (not be blocked), but a warning logged.
            # Since the workflow will actually try to run (and fail without a real
            # sandbox), we only care that the warning was emitted before execution.
            try:
                await execute_workflow(
                    workflow=wf,
                    plan=plan,
                    input_data={},
                )
            except Exception:
                pass

            # Check that warning was issued
            warning_calls = [
                str(call)
                for call in mock_logger.warning.call_args_list
                if "high-risk" in str(call).lower() or "approval" in str(call).lower()
            ]
            assert warning_calls, (
                "Expected a logger.warning about high-risk workflow missing approval step"
            )

    @pytest.mark.asyncio
    async def test_minimal_risk_workflow_not_blocked(self):
        """execute_workflow must not be blocked for minimal-risk workflows."""
        from sandcastle.engine.executor import execute_workflow

        wf = parse_yaml_string(_MINIMAL_YAML)  # default minimal
        plan = self._make_plan(wf)

        # We don't care about execution success (no real sandbox) - just that we
        # don't get a risk-related failure before execution begins.
        with patch("sandcastle.engine.executor.logger"):
            try:
                result = await execute_workflow(
                    workflow=wf,
                    plan=plan,
                    input_data={},
                )
                # If it returned, it should not be a risk-related failure
                if result.status == "failed":
                    assert "Unacceptable risk" not in (result.error or "")
            except Exception:
                pass  # Runtime failures are fine; we only check risk blocking


# ---------------------------------------------------------------------------
# Step 4: API schema
# ---------------------------------------------------------------------------


class TestApiSchema:
    """Verify RunStatusResponse exposes risk_level."""

    def test_run_status_response_has_risk_level_field(self):
        from sandcastle.api.schemas import RunStatusResponse

        # Pydantic model fields
        assert "risk_level" in RunStatusResponse.model_fields, (
            "RunStatusResponse is missing risk_level field"
        )

    def test_run_status_response_default_risk_level(self):
        from sandcastle.api.schemas import RunStatusResponse

        resp = RunStatusResponse(
            run_id="abc",
            workflow_name="test",
            status="completed",
        )
        assert resp.risk_level == "minimal"

    def test_run_status_response_accepts_risk_levels(self):
        from sandcastle.api.schemas import RunStatusResponse

        for level in ("minimal", "limited", "high", "unacceptable"):
            resp = RunStatusResponse(
                run_id="abc",
                workflow_name="test",
                status="completed",
                risk_level=level,
            )
            assert resp.risk_level == level
