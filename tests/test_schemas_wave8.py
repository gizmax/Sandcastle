"""Comprehensive tests for Pydantic API schemas - wave 8 audit.

Tests cover request validation, response constraints, edge cases,
size/depth limits on JSON dicts, enum constraints, cross-field validation,
path traversal prevention, and more.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sandcastle.api.schemas import (
    ApiKeyAllowlistRequest,
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ApiKeyRotateRequest,
    ApiKeyRotateResponse,
    ApiResponse,
    ApprovalRespondRequest,
    ApprovalResponse,
    AutoPilotStatsResponse,
    DeadLetterItemResponse,
    DeadLetterResolveRequest,
    ErrorResponse,
    EvalCaseResponse,
    EvalRunResponse,
    EvalStatsResponse,
    EvalSuiteRunRequest,
    ExperimentResponse,
    ForkRequest,
    GenerateChatMessage,
    GenerateChatRequest,
    HealthResponse,
    LicenseInfoResponse,
    MemoryAddRequest,
    MemoryEntry,
    MemoryListResponse,
    MemorySearchRequest,
    OptimizerStatsResponse,
    PaginationMeta,
    PolicyViolationResponse,
    PolicyViolationStatsResponse,
    ReplayRequest,
    RoutingDecisionResponse,
    RunCompareResponse,
    RunListItem,
    RunStatusResponse,
    RuntimeInfoResponse,
    SampleResponse,
    ScheduleCreateRequest,
    ScheduleResponse,
    ScheduleUpdateRequest,
    SettingsResponse,
    SettingsUpdateRequest,
    StatsResponse,
    StepDiff,
    StepStatusResponse,
    ToolConnectionCreateRequest,
    ToolConnectionUpdateRequest,
    ToolCredentialUpdateRequest,
    ToolListResponse,
    UpdateCheckResponse,
    WorkflowGenerateRequest,
    WorkflowInfoResponse,
    WorkflowPromoteRequest,
    WorkflowRollbackRequest,
    WorkflowRunRequest,
    WorkflowSaveRequest,
    WorkflowVersionDiffResponse,
    WorkflowVersionResponse,
    _MAX_JSON_DICT_BYTES,
    _MAX_JSON_NESTING_DEPTH,
    _check_json_nesting_depth,
    _validate_json_dict,
)


# ---------------------------------------------------------------------------
# Helper: build deeply nested dict
# ---------------------------------------------------------------------------


def _nested_dict(depth: int) -> dict:
    """Build a dict nested to the given depth."""
    d: dict = {"leaf": "value"}
    for _ in range(depth):
        d = {"nested": d}
    return d


# ===========================================================================
# WorkflowRunRequest
# ===========================================================================


class TestWorkflowRunRequest:
    """Tests for WorkflowRunRequest validation."""

    def test_valid_with_workflow(self):
        req = WorkflowRunRequest(workflow="name: test\nsteps: []")
        assert req.workflow is not None
        assert req.workflow_name is None

    def test_valid_with_workflow_name(self):
        req = WorkflowRunRequest(workflow_name="my-workflow")
        assert req.workflow_name == "my-workflow"

    def test_neither_workflow_nor_name_raises(self):
        with pytest.raises(ValidationError, match="workflow.*workflow_name"):
            WorkflowRunRequest()

    def test_both_workflow_and_name_ok(self):
        # Both provided is fine -- routes decide precedence
        req = WorkflowRunRequest(workflow="yaml", workflow_name="name")
        assert req.workflow == "yaml"

    def test_callback_url_must_be_http(self):
        with pytest.raises(ValidationError, match="http"):
            WorkflowRunRequest(
                workflow="x", callback_url="ftp://evil.com/hook"
            )

    def test_callback_url_must_be_https(self):
        req = WorkflowRunRequest(
            workflow="x", callback_url="https://example.com/hook"
        )
        assert req.callback_url == "https://example.com/hook"

    def test_callback_url_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            WorkflowRunRequest(workflow="x", callback_url="  ")

    def test_callback_url_none_allowed(self):
        req = WorkflowRunRequest(workflow="x", callback_url=None)
        assert req.callback_url is None

    def test_version_string_must_be_latest(self):
        with pytest.raises(ValidationError, match="latest"):
            WorkflowRunRequest(workflow="x", version="draft")

    def test_version_string_latest_ok(self):
        req = WorkflowRunRequest(workflow="x", version="latest")
        assert req.version == "latest"

    def test_version_positive_int_ok(self):
        req = WorkflowRunRequest(workflow="x", version=3)
        assert req.version == 3

    def test_version_zero_rejected(self):
        with pytest.raises(ValidationError, match="positive integer"):
            WorkflowRunRequest(workflow="x", version=0)

    def test_version_negative_rejected(self):
        with pytest.raises(ValidationError, match="positive integer"):
            WorkflowRunRequest(workflow="x", version=-1)

    def test_max_cost_usd_negative_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowRunRequest(workflow="x", max_cost_usd=-0.01)

    def test_max_cost_usd_zero_allowed(self):
        req = WorkflowRunRequest(workflow="x", max_cost_usd=0)
        assert req.max_cost_usd == 0

    def test_workflow_max_length(self):
        with pytest.raises(ValidationError):
            WorkflowRunRequest(workflow="x" * 500001)

    def test_workflow_name_max_length(self):
        with pytest.raises(ValidationError):
            WorkflowRunRequest(workflow_name="x" * 201)

    def test_idempotency_key_max_length(self):
        with pytest.raises(ValidationError):
            WorkflowRunRequest(workflow="x", idempotency_key="k" * 201)

    def test_input_dict_oversized_rejected(self):
        # Create a dict whose JSON exceeds 1 MB
        big_val = "x" * (_MAX_JSON_DICT_BYTES + 1)
        with pytest.raises(ValidationError, match="maximum size"):
            WorkflowRunRequest(workflow="x", input={"big": big_val})

    def test_input_dict_deeply_nested_rejected(self):
        deep = _nested_dict(_MAX_JSON_NESTING_DEPTH + 1)
        with pytest.raises(ValidationError, match="nesting depth"):
            WorkflowRunRequest(workflow="x", input=deep)


# ===========================================================================
# ReplayRequest
# ===========================================================================


class TestReplayRequest:
    def test_valid(self):
        r = ReplayRequest(from_step="step1")
        assert r.from_step == "step1"

    def test_empty_step_rejected(self):
        with pytest.raises(ValidationError):
            ReplayRequest(from_step="")

    def test_too_long_step_rejected(self):
        with pytest.raises(ValidationError):
            ReplayRequest(from_step="s" * 101)


# ===========================================================================
# ForkRequest
# ===========================================================================


class TestForkRequest:
    def test_valid(self):
        r = ForkRequest(from_step="step1", changes={"prompt": "new"})
        assert r.changes == {"prompt": "new"}

    def test_empty_changes_ok(self):
        r = ForkRequest(from_step="step1")
        assert r.changes == {}

    def test_oversized_changes_rejected(self):
        big_val = "x" * (_MAX_JSON_DICT_BYTES + 1)
        with pytest.raises(ValidationError, match="maximum size"):
            ForkRequest(from_step="step1", changes={"big": big_val})

    def test_deeply_nested_changes_rejected(self):
        deep = _nested_dict(_MAX_JSON_NESTING_DEPTH + 1)
        with pytest.raises(ValidationError, match="nesting depth"):
            ForkRequest(from_step="step1", changes=deep)


# ===========================================================================
# ScheduleCreateRequest
# ===========================================================================


class TestScheduleCreateRequest:
    def test_valid(self):
        r = ScheduleCreateRequest(
            workflow_name="my-wf",
            cron_expression="0 9 * * 1",
        )
        assert r.cron_expression == "0 9 * * 1"

    def test_empty_workflow_name_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(workflow_name="", cron_expression="0 9 * * 1")

    def test_cron_four_fields_rejected(self):
        with pytest.raises(ValidationError, match="5 fields"):
            ScheduleCreateRequest(
                workflow_name="wf", cron_expression="0 9 * *"
            )

    def test_cron_six_fields_rejected(self):
        with pytest.raises(ValidationError, match="5 fields"):
            ScheduleCreateRequest(
                workflow_name="wf", cron_expression="0 0 9 * * 1"
            )

    def test_cron_empty_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(workflow_name="wf", cron_expression="")

    def test_input_data_oversized_rejected(self):
        big = {"k": "v" * (_MAX_JSON_DICT_BYTES + 1)}
        with pytest.raises(ValidationError, match="maximum size"):
            ScheduleCreateRequest(
                workflow_name="wf",
                cron_expression="0 9 * * 1",
                input_data=big,
            )

    def test_notify_validated(self):
        deep = _nested_dict(_MAX_JSON_NESTING_DEPTH + 1)
        with pytest.raises(ValidationError, match="nesting depth"):
            ScheduleCreateRequest(
                workflow_name="wf",
                cron_expression="0 9 * * 1",
                notify=deep,
            )

    def test_notify_none_ok(self):
        r = ScheduleCreateRequest(
            workflow_name="wf",
            cron_expression="0 9 * * 1",
            notify=None,
        )
        assert r.notify is None


# ===========================================================================
# ScheduleUpdateRequest
# ===========================================================================


class TestScheduleUpdateRequest:
    def test_valid_partial(self):
        r = ScheduleUpdateRequest(enabled=False)
        assert r.enabled is False
        assert r.cron_expression is None

    def test_cron_empty_string_rejected(self):
        """min_length=1 should reject empty string."""
        with pytest.raises(ValidationError):
            ScheduleUpdateRequest(cron_expression="")

    def test_cron_invalid_field_count_rejected(self):
        with pytest.raises(ValidationError, match="5 fields"):
            ScheduleUpdateRequest(cron_expression="* *")

    def test_cron_none_ok(self):
        r = ScheduleUpdateRequest(cron_expression=None)
        assert r.cron_expression is None


# ===========================================================================
# WorkflowGenerateRequest
# ===========================================================================


class TestWorkflowGenerateRequest:
    def test_valid(self):
        r = WorkflowGenerateRequest(description="Build a report")
        assert r.description == "Build a report"

    def test_empty_description_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowGenerateRequest(description="")

    def test_refine_from_max_length(self):
        with pytest.raises(ValidationError):
            WorkflowGenerateRequest(
                description="test", refine_from="x" * 500001
            )


# ===========================================================================
# GenerateChatMessage / GenerateChatRequest
# ===========================================================================


class TestGenerateChatMessage:
    def test_valid_user(self):
        m = GenerateChatMessage(role="user", content="hello")
        assert m.role == "user"

    def test_valid_assistant(self):
        m = GenerateChatMessage(role="assistant", content="hi")
        assert m.role == "assistant"

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            GenerateChatMessage(role="system", content="hi")

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            GenerateChatMessage(role="user", content="")


class TestGenerateChatRequest:
    def test_valid(self):
        r = GenerateChatRequest(
            messages=[GenerateChatMessage(role="user", content="hello")]
        )
        assert len(r.messages) == 1

    def test_empty_messages_rejected(self):
        with pytest.raises(ValidationError):
            GenerateChatRequest(messages=[])

    def test_max_500_messages(self):
        """List capped at 500 entries."""
        msgs = [GenerateChatMessage(role="user", content="x")] * 501
        with pytest.raises(ValidationError):
            GenerateChatRequest(messages=msgs)

    def test_500_messages_ok(self):
        msgs = [GenerateChatMessage(role="user", content="x")] * 500
        r = GenerateChatRequest(messages=msgs)
        assert len(r.messages) == 500


# ===========================================================================
# WorkflowSaveRequest
# ===========================================================================


class TestWorkflowSaveRequest:
    def test_valid(self):
        r = WorkflowSaveRequest(name="my-wf", content="steps: []")
        assert r.name == "my-wf"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowSaveRequest(name="", content="x")

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowSaveRequest(name="wf", content="")

    def test_path_traversal_slash_rejected(self):
        with pytest.raises(ValidationError, match="must not contain"):
            WorkflowSaveRequest(name="../evil", content="x")

    def test_path_traversal_backslash_rejected(self):
        with pytest.raises(ValidationError, match="must not contain"):
            WorkflowSaveRequest(name="a\\b", content="x")

    def test_path_traversal_dotdot_rejected(self):
        with pytest.raises(ValidationError, match="must not contain"):
            WorkflowSaveRequest(name="foo..bar", content="x")

    def test_leading_whitespace_rejected(self):
        with pytest.raises(ValidationError, match="whitespace"):
            WorkflowSaveRequest(name=" test", content="x")

    def test_trailing_whitespace_rejected(self):
        with pytest.raises(ValidationError, match="whitespace"):
            WorkflowSaveRequest(name="test ", content="x")


# ===========================================================================
# ApiKeyCreateRequest
# ===========================================================================


class TestApiKeyCreateRequest:
    def test_valid(self):
        r = ApiKeyCreateRequest(name="my-key")
        assert r.name == "my-key"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ApiKeyCreateRequest(name="")

    def test_whitespace_only_name_rejected(self):
        with pytest.raises(ValidationError, match="whitespace"):
            ApiKeyCreateRequest(name="   ")

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            ApiKeyCreateRequest(name="key", max_cost_per_run_usd=-1.0)

    def test_tenant_id_max_length(self):
        with pytest.raises(ValidationError):
            ApiKeyCreateRequest(name="key", tenant_id="t" * 201)


# ===========================================================================
# ApiKeyRotateRequest
# ===========================================================================


class TestApiKeyRotateRequest:
    def test_valid_defaults(self):
        r = ApiKeyRotateRequest()
        assert r.grace_period_hours is None

    def test_valid_custom(self):
        r = ApiKeyRotateRequest(grace_period_hours=48)
        assert r.grace_period_hours == 48

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            ApiKeyRotateRequest(grace_period_hours=-1)

    def test_max_one_year(self):
        """Grace period capped at 8760 hours (1 year)."""
        with pytest.raises(ValidationError):
            ApiKeyRotateRequest(grace_period_hours=8761)

    def test_boundary_8760_ok(self):
        r = ApiKeyRotateRequest(grace_period_hours=8760)
        assert r.grace_period_hours == 8760


# ===========================================================================
# ApiKeyAllowlistRequest
# ===========================================================================


class TestApiKeyAllowlistRequest:
    def test_valid(self):
        r = ApiKeyAllowlistRequest(cidrs=["10.0.0.0/8", "::1/128"])
        assert len(r.cidrs) == 2

    def test_empty_list_ok(self):
        r = ApiKeyAllowlistRequest(cidrs=[])
        assert r.cidrs == []

    def test_max_1000_entries(self):
        with pytest.raises(ValidationError):
            ApiKeyAllowlistRequest(cidrs=["10.0.0.0/8"] * 1001)

    def test_1000_entries_ok(self):
        r = ApiKeyAllowlistRequest(cidrs=["10.0.0.0/8"] * 1000)
        assert len(r.cidrs) == 1000

    def test_cidr_entry_too_long_rejected(self):
        with pytest.raises(ValidationError, match="too long"):
            ApiKeyAllowlistRequest(cidrs=["x" * 51])

    def test_empty_cidr_entry_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            ApiKeyAllowlistRequest(cidrs=[""])

    def test_whitespace_cidr_entry_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            ApiKeyAllowlistRequest(cidrs=["   "])


# ===========================================================================
# WorkflowPromoteRequest / WorkflowRollbackRequest
# ===========================================================================


class TestWorkflowPromoteRequest:
    def test_valid_none(self):
        r = WorkflowPromoteRequest()
        assert r.version is None

    def test_valid_positive(self):
        r = WorkflowPromoteRequest(version=3)
        assert r.version == 3

    def test_zero_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowPromoteRequest(version=0)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowRollbackRequest(target_version=-1)


class TestWorkflowRollbackRequest:
    def test_valid_none(self):
        r = WorkflowRollbackRequest()
        assert r.target_version is None

    def test_valid_positive(self):
        r = WorkflowRollbackRequest(target_version=2)
        assert r.target_version == 2

    def test_zero_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowRollbackRequest(target_version=0)


# ===========================================================================
# ApprovalRespondRequest
# ===========================================================================


class TestApprovalRespondRequest:
    def test_valid(self):
        r = ApprovalRespondRequest(comment="Looks good")
        assert r.comment == "Looks good"

    def test_edited_data_oversized_rejected(self):
        big_val = "x" * (_MAX_JSON_DICT_BYTES + 1)
        with pytest.raises(ValidationError, match="maximum size"):
            ApprovalRespondRequest(edited_data={"big": big_val})

    def test_edited_data_deeply_nested_rejected(self):
        deep = _nested_dict(_MAX_JSON_NESTING_DEPTH + 1)
        with pytest.raises(ValidationError, match="nesting depth"):
            ApprovalRespondRequest(edited_data=deep)

    def test_comment_max_length(self):
        with pytest.raises(ValidationError):
            ApprovalRespondRequest(comment="c" * 5001)


# ===========================================================================
# PaginationMeta
# ===========================================================================


class TestPaginationMeta:
    def test_valid(self):
        p = PaginationMeta(total=100, limit=10, offset=0)
        assert p.total == 100

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            PaginationMeta(total=-1, limit=10, offset=0)

    def test_zero_limit_rejected(self):
        with pytest.raises(ValidationError):
            PaginationMeta(total=0, limit=0, offset=0)

    def test_negative_offset_rejected(self):
        with pytest.raises(ValidationError):
            PaginationMeta(total=0, limit=1, offset=-1)

    def test_total_zero_ok(self):
        p = PaginationMeta(total=0, limit=1, offset=0)
        assert p.total == 0


# ===========================================================================
# RunStatusResponse
# ===========================================================================


class TestRunStatusResponse:
    def test_valid_minimal(self):
        r = RunStatusResponse(
            run_id="abc-123",
            workflow_name="test",
            status="running",
        )
        assert r.total_cost_usd == 0.0
        assert r.depth == 0

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            RunStatusResponse(
                run_id="x",
                workflow_name="w",
                status="running",
                total_cost_usd=-1.0,
            )

    def test_negative_max_cost_rejected(self):
        with pytest.raises(ValidationError):
            RunStatusResponse(
                run_id="x",
                workflow_name="w",
                status="running",
                max_cost_usd=-0.01,
            )

    def test_negative_depth_rejected(self):
        with pytest.raises(ValidationError):
            RunStatusResponse(
                run_id="x",
                workflow_name="w",
                status="running",
                depth=-1,
            )


# ===========================================================================
# StepStatusResponse
# ===========================================================================


class TestStepStatusResponse:
    def test_valid(self):
        s = StepStatusResponse(step_id="s1", status="completed")
        assert s.cost_usd == 0.0
        assert s.attempt == 1

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            StepStatusResponse(step_id="s1", status="done", cost_usd=-1)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            StepStatusResponse(
                step_id="s1", status="done", duration_seconds=-0.1
            )

    def test_zero_attempt_rejected(self):
        with pytest.raises(ValidationError):
            StepStatusResponse(step_id="s1", status="done", attempt=0)


# ===========================================================================
# StepDiff
# ===========================================================================


class TestStepDiff:
    def test_valid(self):
        d = StepDiff(step_id="s1", presence="both")
        assert d.cost_a == 0.0

    def test_negative_cost_a_rejected(self):
        with pytest.raises(ValidationError):
            StepDiff(step_id="s1", presence="both", cost_a=-1)

    def test_negative_duration_a_rejected(self):
        with pytest.raises(ValidationError):
            StepDiff(step_id="s1", presence="both", duration_a=-1)

    def test_cost_delta_can_be_negative(self):
        """Cost delta is allowed to be negative (run_b cheaper)."""
        d = StepDiff(step_id="s1", presence="both", cost_delta=-0.5)
        assert d.cost_delta == -0.5


# ===========================================================================
# StatsResponse
# ===========================================================================


class TestStatsResponse:
    def test_defaults(self):
        s = StatsResponse()
        assert s.total_runs_today == 0
        assert s.success_rate == 0.0

    def test_negative_runs_rejected(self):
        with pytest.raises(ValidationError):
            StatsResponse(total_runs_today=-1)

    def test_success_rate_over_100_rejected(self):
        with pytest.raises(ValidationError):
            StatsResponse(success_rate=101.0)

    def test_negative_success_rate_rejected(self):
        with pytest.raises(ValidationError):
            StatsResponse(success_rate=-1)


# ===========================================================================
# WorkflowInfoResponse
# ===========================================================================


class TestWorkflowInfoResponse:
    def test_valid(self):
        r = WorkflowInfoResponse(
            name="test",
            description="A workflow",
            steps_count=3,
            file_name="test.yaml",
        )
        assert r.steps_count == 3

    def test_negative_steps_count_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowInfoResponse(
                name="t",
                description="d",
                steps_count=-1,
                file_name="t.yaml",
            )


# ===========================================================================
# WorkflowVersionResponse
# ===========================================================================


class TestWorkflowVersionResponse:
    def test_valid(self):
        r = WorkflowVersionResponse(
            id="v1", workflow_name="wf", version=1, status="draft"
        )
        assert r.version == 1

    def test_version_zero_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowVersionResponse(
                id="v1", workflow_name="wf", version=0, status="draft"
            )


# ===========================================================================
# WorkflowVersionDiffResponse
# ===========================================================================


class TestWorkflowVersionDiffResponse:
    def test_valid(self):
        r = WorkflowVersionDiffResponse(
            version_a=1, version_b=2, yaml_a="a", yaml_b="b"
        )
        assert r.version_a == 1

    def test_version_zero_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowVersionDiffResponse(
                version_a=0, version_b=1, yaml_a="a", yaml_b="b"
            )


# ===========================================================================
# RoutingDecisionResponse
# ===========================================================================


class TestRoutingDecisionResponse:
    def test_valid(self):
        r = RoutingDecisionResponse(
            id="r1",
            run_id="run1",
            step_id="s1",
            selected_model="sonnet",
            selected_variant_id="v1",
        )
        assert r.confidence == 0.1
        assert r.budget_pressure == 0.0

    def test_confidence_over_1_rejected(self):
        with pytest.raises(ValidationError):
            RoutingDecisionResponse(
                id="r1",
                run_id="run1",
                step_id="s1",
                selected_model="sonnet",
                selected_variant_id="v1",
                confidence=1.5,
            )

    def test_budget_pressure_over_1_rejected(self):
        with pytest.raises(ValidationError):
            RoutingDecisionResponse(
                id="r1",
                run_id="run1",
                step_id="s1",
                selected_model="sonnet",
                selected_variant_id="v1",
                budget_pressure=1.1,
            )

    def test_negative_confidence_rejected(self):
        with pytest.raises(ValidationError):
            RoutingDecisionResponse(
                id="r1",
                run_id="run1",
                step_id="s1",
                selected_model="sonnet",
                selected_variant_id="v1",
                confidence=-0.1,
            )


# ===========================================================================
# OptimizerStatsResponse
# ===========================================================================


class TestOptimizerStatsResponse:
    def test_defaults(self):
        r = OptimizerStatsResponse()
        assert r.avg_confidence == 0.0

    def test_confidence_over_1_rejected(self):
        with pytest.raises(ValidationError):
            OptimizerStatsResponse(avg_confidence=2.0)

    def test_negative_alerts_rejected(self):
        with pytest.raises(ValidationError):
            OptimizerStatsResponse(active_alerts=-1)


# ===========================================================================
# SettingsResponse
# ===========================================================================


class TestSettingsResponse:
    def test_defaults(self):
        r = SettingsResponse()
        assert r.max_workflow_depth == 5
        assert r.default_max_cost_usd == 0.0

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            SettingsResponse(default_max_cost_usd=-1)

    def test_max_workflow_depth_bounds(self):
        with pytest.raises(ValidationError):
            SettingsResponse(max_workflow_depth=0)
        with pytest.raises(ValidationError):
            SettingsResponse(max_workflow_depth=21)


# ===========================================================================
# SettingsUpdateRequest
# ===========================================================================


class TestSettingsUpdateRequest:
    def test_valid(self):
        r = SettingsUpdateRequest(log_level="debug")
        assert r.log_level == "debug"

    def test_invalid_log_level(self):
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(log_level="trace")

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(default_max_cost_usd=-1)

    def test_workflow_depth_bounds(self):
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(max_workflow_depth=0)
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(max_workflow_depth=21)

    def test_api_key_max_length(self):
        with pytest.raises(ValidationError):
            SettingsUpdateRequest(anthropic_api_key="k" * 501)

    def test_all_none_ok(self):
        r = SettingsUpdateRequest()
        assert r.anthropic_api_key is None


# ===========================================================================
# ToolCredentialUpdateRequest
# ===========================================================================


class TestToolCredentialUpdateRequest:
    def test_valid(self):
        r = ToolCredentialUpdateRequest(
            credentials={"TOOL_SLACK_BOT_TOKEN": "xoxb-123"}
        )
        assert len(r.credentials) == 1

    def test_empty_credentials_allowed(self):
        r = ToolCredentialUpdateRequest(credentials={})
        assert r.credentials == {}

    def test_too_many_credentials_rejected(self):
        creds = {f"KEY_{i}": "val" for i in range(51)}
        with pytest.raises(ValidationError, match="50"):
            ToolCredentialUpdateRequest(credentials=creds)

    def test_key_too_long_rejected(self):
        with pytest.raises(ValidationError, match="key too long"):
            ToolCredentialUpdateRequest(credentials={"K" * 201: "val"})

    def test_value_too_long_rejected(self):
        with pytest.raises(ValidationError, match="value too long"):
            ToolCredentialUpdateRequest(credentials={"KEY": "v" * 10001})

    def test_empty_key_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            ToolCredentialUpdateRequest(credentials={"  ": "val"})


# ===========================================================================
# ToolConnectionCreateRequest
# ===========================================================================


class TestToolConnectionCreateRequest:
    def test_valid(self):
        r = ToolConnectionCreateRequest(
            name="staging", credentials={"KEY": "val"}
        )
        assert r.name == "staging"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ToolConnectionCreateRequest(name="", credentials={"K": "v"})

    def test_empty_credentials_rejected(self):
        with pytest.raises(ValidationError, match="At least one"):
            ToolConnectionCreateRequest(name="test", credentials={})


# ===========================================================================
# ToolConnectionUpdateRequest
# ===========================================================================


class TestToolConnectionUpdateRequest:
    def test_valid(self):
        r = ToolConnectionUpdateRequest(credentials={"KEY": "val"})
        assert len(r.credentials) == 1

    def test_empty_credentials_rejected(self):
        with pytest.raises(ValidationError, match="At least one"):
            ToolConnectionUpdateRequest(credentials={})

    def test_too_many_rejected(self):
        creds = {f"KEY_{i}": "val" for i in range(51)}
        with pytest.raises(ValidationError, match="50"):
            ToolConnectionUpdateRequest(credentials=creds)


# ===========================================================================
# EvalSuiteRunRequest
# ===========================================================================


class TestEvalSuiteRunRequest:
    def test_valid(self):
        r = EvalSuiteRunRequest(suite_yaml="name: test\ncases: []")
        assert r.concurrency == 1

    def test_empty_yaml_rejected(self):
        with pytest.raises(ValidationError):
            EvalSuiteRunRequest(suite_yaml="")

    def test_concurrency_bounds(self):
        with pytest.raises(ValidationError):
            EvalSuiteRunRequest(suite_yaml="x", concurrency=0)
        with pytest.raises(ValidationError):
            EvalSuiteRunRequest(suite_yaml="x", concurrency=21)


# ===========================================================================
# EvalRunResponse
# ===========================================================================


class TestEvalRunResponse:
    def test_valid(self):
        r = EvalRunResponse(
            id="e1", suite_name="suite", workflow_name="wf", status="completed"
        )
        assert r.pass_rate == 0.0

    def test_pass_rate_over_100_rejected(self):
        with pytest.raises(ValidationError):
            EvalRunResponse(
                id="e1",
                suite_name="suite",
                workflow_name="wf",
                status="done",
                pass_rate=101,
            )

    def test_negative_total_cases_rejected(self):
        with pytest.raises(ValidationError):
            EvalRunResponse(
                id="e1",
                suite_name="s",
                workflow_name="w",
                status="done",
                total_cases=-1,
            )


# ===========================================================================
# EvalStatsResponse
# ===========================================================================


class TestEvalStatsResponse:
    def test_defaults(self):
        s = EvalStatsResponse()
        assert s.avg_pass_rate == 0.0

    def test_pass_rate_over_100_rejected(self):
        with pytest.raises(ValidationError):
            EvalStatsResponse(avg_pass_rate=150)


# ===========================================================================
# MemoryAddRequest
# ===========================================================================


class TestMemoryAddRequest:
    def test_valid(self):
        r = MemoryAddRequest(
            scope="agent",
            scope_id="my-agent",
            content="Remember this",
        )
        assert r.scope == "agent"

    def test_invalid_scope_rejected(self):
        with pytest.raises(ValidationError):
            MemoryAddRequest(
                scope="invalid",
                scope_id="test",
                content="x",
            )

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            MemoryAddRequest(scope="global", scope_id="test", content="")

    def test_metadata_oversized_rejected(self):
        big = {"k": "v" * (_MAX_JSON_DICT_BYTES + 1)}
        with pytest.raises(ValidationError, match="maximum size"):
            MemoryAddRequest(
                scope="workflow",
                scope_id="test",
                content="x",
                metadata=big,
            )

    def test_metadata_deeply_nested_rejected(self):
        deep = _nested_dict(_MAX_JSON_NESTING_DEPTH + 1)
        with pytest.raises(ValidationError, match="nesting depth"):
            MemoryAddRequest(
                scope="workflow",
                scope_id="test",
                content="x",
                metadata=deep,
            )

    def test_metadata_none_ok(self):
        r = MemoryAddRequest(scope="global", scope_id="test", content="hi")
        assert r.metadata is None


# ===========================================================================
# MemorySearchRequest
# ===========================================================================


class TestMemorySearchRequest:
    def test_valid(self):
        r = MemorySearchRequest(scope_id="test", query="find stuff")
        assert r.limit == 10

    def test_empty_query_rejected(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(scope_id="test", query="")

    def test_limit_bounds(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(scope_id="test", query="x", limit=0)
        with pytest.raises(ValidationError):
            MemorySearchRequest(scope_id="test", query="x", limit=201)


# ===========================================================================
# MemoryListResponse
# ===========================================================================


class TestMemoryListResponse:
    def test_valid(self):
        r = MemoryListResponse(memories=[], total=0)
        assert r.total == 0

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            MemoryListResponse(memories=[], total=-1)


# ===========================================================================
# AutoPilotStatsResponse
# ===========================================================================


class TestAutoPilotStatsResponse:
    def test_defaults(self):
        s = AutoPilotStatsResponse()
        assert s.total_experiments == 0

    def test_negative_experiments_rejected(self):
        with pytest.raises(ValidationError):
            AutoPilotStatsResponse(total_experiments=-1)

    def test_negative_active_rejected(self):
        with pytest.raises(ValidationError):
            AutoPilotStatsResponse(active_experiments=-1)


# ===========================================================================
# SampleResponse
# ===========================================================================


class TestSampleResponse:
    def test_valid(self):
        r = SampleResponse(id="s1", experiment_id="e1", variant_id="v1")
        assert r.cost_usd == 0.0

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            SampleResponse(
                id="s1", experiment_id="e1", variant_id="v1", cost_usd=-1
            )

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            SampleResponse(
                id="s1",
                experiment_id="e1",
                variant_id="v1",
                duration_seconds=-1,
            )


# ===========================================================================
# EvalCaseResponse
# ===========================================================================


class TestEvalCaseResponse:
    def test_valid(self):
        r = EvalCaseResponse(case_name="test1", passed=True)
        assert r.cost_usd == 0.0

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            EvalCaseResponse(case_name="t", passed=True, cost_usd=-1)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            EvalCaseResponse(
                case_name="t", passed=True, duration_seconds=-0.01
            )


# ===========================================================================
# DeadLetterItemResponse
# ===========================================================================


class TestDeadLetterItemResponse:
    def test_valid(self):
        r = DeadLetterItemResponse(id="d1", run_id="r1", step_id="s1")
        assert r.attempts == 1

    def test_zero_attempts_rejected(self):
        with pytest.raises(ValidationError):
            DeadLetterItemResponse(
                id="d1", run_id="r1", step_id="s1", attempts=0
            )


# ===========================================================================
# LicenseInfoResponse
# ===========================================================================


class TestLicenseInfoResponse:
    def test_valid(self):
        r = LicenseInfoResponse(status="valid", tier="pro")
        assert r.max_seats == 0

    def test_negative_seats_rejected(self):
        with pytest.raises(ValidationError):
            LicenseInfoResponse(status="valid", tier="pro", max_seats=-1)


# ===========================================================================
# PolicyViolationStatsResponse
# ===========================================================================


class TestPolicyViolationStatsResponse:
    def test_defaults(self):
        r = PolicyViolationStatsResponse()
        assert r.total_violations_30d == 0

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            PolicyViolationStatsResponse(total_violations_30d=-1)


# ===========================================================================
# ToolListResponse
# ===========================================================================


class TestToolListResponse:
    def test_defaults(self):
        r = ToolListResponse()
        assert r.total == 0

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            ToolListResponse(total=-1)


# ===========================================================================
# RunCompareResponse
# ===========================================================================


class TestRunCompareResponse:
    def _item(self, **kwargs):
        defaults = dict(
            run_id="r1",
            workflow_name="wf",
            status="completed",
        )
        defaults.update(kwargs)
        return RunListItem(**defaults)

    def test_valid(self):
        r = RunCompareResponse(
            run_a=self._item(run_id="r1"),
            run_b=self._item(run_id="r2"),
        )
        assert r.total_cost_delta == 0.0

    def test_negative_cost_a_rejected(self):
        with pytest.raises(ValidationError):
            RunCompareResponse(
                run_a=self._item(),
                run_b=self._item(),
                total_cost_a=-1,
            )

    def test_cost_delta_can_be_negative(self):
        r = RunCompareResponse(
            run_a=self._item(),
            run_b=self._item(),
            total_cost_delta=-5.0,
        )
        assert r.total_cost_delta == -5.0


# ===========================================================================
# RunListItem
# ===========================================================================


class TestRunListItem:
    def test_valid(self):
        r = RunListItem(run_id="r1", workflow_name="wf", status="completed")
        assert r.total_cost_usd == 0.0

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            RunListItem(
                run_id="r1",
                workflow_name="wf",
                status="done",
                total_cost_usd=-1,
            )


# ===========================================================================
# JSON dict size/nesting helpers
# ===========================================================================


class TestJsonDictValidation:
    def test_validate_json_dict_none_ok(self):
        assert _validate_json_dict(None, "field") is None

    def test_validate_json_dict_small_ok(self):
        d = {"key": "value"}
        assert _validate_json_dict(d, "field") == d

    def test_validate_json_dict_oversized(self):
        big = {"k": "v" * (_MAX_JSON_DICT_BYTES + 1)}
        with pytest.raises(ValueError, match="maximum size"):
            _validate_json_dict(big, "test_field")

    def test_check_nesting_depth_at_limit(self):
        """Exactly at the limit should not raise.

        _nested_dict(n) creates n wrapper dicts + 1 leaf dict = n+1 total
        dict levels. The depth check counts from 0, so depth 20 means 21
        recursive calls at the leaf. Use n-1 wrappers so the total dict
        nesting is exactly _MAX_JSON_NESTING_DEPTH.
        """
        d = _nested_dict(_MAX_JSON_NESTING_DEPTH - 1)
        _check_json_nesting_depth(d)  # should not raise

    def test_check_nesting_depth_over_limit(self):
        d = _nested_dict(_MAX_JSON_NESTING_DEPTH)
        with pytest.raises(ValueError, match="nesting depth"):
            _check_json_nesting_depth(d)

    def test_check_nesting_depth_list(self):
        """Nested lists also count toward depth."""
        obj: list = ["leaf"]
        for _ in range(_MAX_JSON_NESTING_DEPTH + 1):
            obj = [obj]
        with pytest.raises(ValueError, match="nesting depth"):
            _check_json_nesting_depth(obj)

    def test_scalar_values_no_depth_issue(self):
        _check_json_nesting_depth("hello")
        _check_json_nesting_depth(42)
        _check_json_nesting_depth(None)


# ===========================================================================
# Serialization round-trip checks
# ===========================================================================


class TestSerializationRoundtrip:
    def test_run_status_with_datetime(self):
        now = datetime.now(timezone.utc)
        r = RunStatusResponse(
            run_id="r1",
            workflow_name="wf",
            status="completed",
            started_at=now,
            completed_at=now,
        )
        d = r.model_dump()
        assert isinstance(d["started_at"], datetime)

    def test_api_response_with_error(self):
        resp = ApiResponse(
            error=ErrorResponse(code="TEST", message="A test error")
        )
        d = resp.model_dump()
        assert d["error"]["code"] == "TEST"
        assert d["data"] is None

    def test_api_response_with_data_and_meta(self):
        resp = ApiResponse(
            data={"run_id": "x"},
            meta=PaginationMeta(total=1, limit=10, offset=0),
        )
        d = resp.model_dump()
        assert d["meta"]["total"] == 1

    def test_json_serialization_of_response(self):
        r = RunListItem(
            run_id="r1",
            workflow_name="wf",
            status="completed",
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        j = r.model_dump_json()
        parsed = json.loads(j)
        assert parsed["run_id"] == "r1"
        assert "2025" in parsed["started_at"]

    def test_api_key_response_excludes_plaintext(self):
        """ApiKeyResponse should not have a 'key' field (plaintext)."""
        r = ApiKeyResponse(
            id="k1", name="test", is_active=True
        )
        d = r.model_dump()
        assert "key" not in d

    def test_api_key_created_response_includes_key(self):
        """ApiKeyCreatedResponse should include the plaintext key."""
        r = ApiKeyCreatedResponse(
            id="k1", key_prefix="sc_", name="test", key="sc_full_key_here"
        )
        d = r.model_dump()
        assert d["key"] == "sc_full_key_here"


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_workflow_run_request_empty_input_ok(self):
        r = WorkflowRunRequest(workflow="x", input={})
        assert r.input == {}

    def test_dead_letter_resolve_none_reason_ok(self):
        r = DeadLetterResolveRequest()
        assert r.reason is None

    def test_dead_letter_resolve_empty_reason_ok(self):
        """Empty string is allowed for reason (optional field)."""
        r = DeadLetterResolveRequest(reason="")
        assert r.reason == ""

    def test_health_response_redis_none(self):
        r = HealthResponse(status="ok", runtime=True, database=True)
        assert r.redis is None

    def test_approval_response_defaults(self):
        r = ApprovalResponse(
            id="a1", run_id="r1", step_id="s1", status="pending"
        )
        assert r.on_timeout == "abort"
        assert r.allow_edit is False

    def test_experiment_response_defaults(self):
        r = ExperimentResponse(
            id="e1", workflow_name="wf", step_id="s1", status="running"
        )
        assert r.optimize_for == "quality"

    def test_callback_url_http_allowed(self):
        """HTTP (not just HTTPS) is allowed for local dev."""
        r = WorkflowRunRequest(
            workflow="x", callback_url="http://localhost:8080/hook"
        )
        assert r.callback_url.startswith("http://")

    def test_workflow_save_name_with_hyphen_ok(self):
        r = WorkflowSaveRequest(name="my-cool-workflow", content="steps: []")
        assert r.name == "my-cool-workflow"

    def test_workflow_save_name_with_underscore_ok(self):
        r = WorkflowSaveRequest(name="my_workflow_v2", content="steps: []")
        assert r.name == "my_workflow_v2"
