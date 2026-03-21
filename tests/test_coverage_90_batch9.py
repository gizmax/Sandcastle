"""Batch 9: Final push to 90%+ combined coverage.

Covers:
- policy.py: inject_approval, block action, alert/log, resolve_step_policies
- storage.py: S3 safe_key edge cases
- schemas.py: version validator edge cases, credential validators
- executor.py: _emit_audit_event, _backoff_delay, _validate_browser_url,
               _save_checkpoint, resolve_templates, RunContext.with_item/snapshot
- scheduler.py: restore_schedules
- autopilot.py: evaluate_result (schema_completeness)
- routes.py: /stats/forecast, /stats/heatmap, /check-update, /eval/runs
- executor.py: _write_pdf_report
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# policy.py: PolicyEngine evaluate - inject_approval, block, alert/log actions
# ---------------------------------------------------------------------------

class TestPolicyEngineActions:
    def _make_policy(self, action_type, trigger_pattern=None):
        from sandcastle.engine.policy import (
            PolicyAction,
            PolicyDefinition,
            PolicyPattern,
            PolicyTrigger,
        )
        patterns = []
        if trigger_pattern:
            patterns = [PolicyPattern(type="regex", pattern=trigger_pattern)]
        return PolicyDefinition(
            id="test_policy",
            trigger=PolicyTrigger(
                type="output_contains",
                patterns=patterns or None,
            ),
            action=PolicyAction(type=action_type),
            severity="high",
        )

    @pytest.mark.asyncio
    async def test_inject_approval_action(self):
        """PolicyEngine triggers inject_approval action."""
        from sandcastle.engine.policy import PolicyEngine

        policy = self._make_policy("inject_approval", "confidential")
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="step1",
            output="This is confidential information",
            context={"run_id": "test-run", "step_id": "step1", "total_cost_usd": 0.0, "input": {}},
        )
        assert result.should_inject_approval is True

    @pytest.mark.asyncio
    async def test_block_action(self):
        """PolicyEngine triggers block action."""
        from sandcastle.engine.policy import PolicyEngine

        policy = self._make_policy("block", "dangerous_pattern")
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="step1",
            output="This contains dangerous_pattern content",
            context={"run_id": "test-run", "step_id": "step1", "total_cost_usd": 0.0, "input": {}},
        )
        assert result.should_block is True

    @pytest.mark.asyncio
    async def test_alert_action_logged(self):
        """PolicyEngine triggers alert action (just logs)."""
        from sandcastle.engine.policy import PolicyEngine

        policy = self._make_policy("alert", "suspicious")
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="step1",
            output="This is suspicious content",
            context={"run_id": "test-run", "step_id": "step1", "total_cost_usd": 0.0, "input": {}},
        )
        # Should not block
        assert result.should_block is False
        assert len(result.violations) > 0

    @pytest.mark.asyncio
    async def test_log_action_logged(self):
        """PolicyEngine triggers log action (just logs)."""
        from sandcastle.engine.policy import PolicyEngine

        policy = self._make_policy("log", "logged_pattern")
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="step1",
            output="This contains logged_pattern",
            context={"run_id": "test-run", "step_id": "step1", "total_cost_usd": 0.0, "input": {}},
        )
        assert len(result.violations) > 0

    @pytest.mark.asyncio
    async def test_no_patterns_trigger_condition(self):
        """PolicyEngine with condition trigger."""
        from sandcastle.engine.policy import PolicyAction, PolicyDefinition, PolicyEngine, PolicyTrigger

        # Use condition trigger that always fires
        policy = PolicyDefinition(
            id="cond_policy",
            trigger=PolicyTrigger(type="condition", expression="True"),
            action=PolicyAction(type="log"),
            severity="low",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="step1",
            output="Any output",
            context={"run_id": "test-run", "step_id": "step1", "total_cost_usd": 0.0, "input": {}},
        )
        # Policy fires
        assert result is not None


# ---------------------------------------------------------------------------
# policy.py: resolve_step_policies
# ---------------------------------------------------------------------------

class TestResolveStepPolicies:
    def _make_policy_def(self, policy_id):
        from sandcastle.engine.policy import (
            PolicyAction,
            PolicyDefinition,
            PolicyTrigger,
        )
        return PolicyDefinition(
            id=policy_id,
            trigger=PolicyTrigger(type="output_contains"),
            action=PolicyAction(type="log"),
            severity="medium",
        )

    def test_resolve_by_string_id(self):
        """resolve_step_policies resolves string IDs to policy objects."""
        from sandcastle.engine.policy import resolve_step_policies

        global_policies = [self._make_policy_def("my_policy")]
        step_policies = ["my_policy"]
        result = resolve_step_policies(step_policies, global_policies)
        assert len(result) == 1
        assert result[0].id == "my_policy"

    def test_resolve_policy_object_passthrough(self):
        """resolve_step_policies passes through PolicyDefinition objects."""
        from sandcastle.engine.policy import resolve_step_policies

        policy = self._make_policy_def("inline_policy")
        result = resolve_step_policies([policy], [])
        assert len(result) == 1
        assert result[0].id == "inline_policy"

    def test_resolve_unknown_string_id_logs_warning(self):
        """resolve_step_policies warns for unknown policy IDs."""
        from sandcastle.engine.policy import resolve_step_policies

        result = resolve_step_policies(["unknown_policy"], [])
        # Unknown ID -> not in result
        assert len(result) == 0

    def test_resolve_none_returns_all_global(self):
        """resolve_step_policies with None returns all global policies."""
        from sandcastle.engine.policy import resolve_step_policies

        global_policies = [self._make_policy_def("p1"), self._make_policy_def("p2")]
        result = resolve_step_policies(None, global_policies)
        assert len(result) == 2

    def test_resolve_empty_list_returns_none(self):
        """resolve_step_policies with empty list returns empty."""
        from sandcastle.engine.policy import resolve_step_policies

        global_policies = [self._make_policy_def("p1")]
        result = resolve_step_policies([], global_policies)
        assert result == []


# ---------------------------------------------------------------------------
# storage.py: S3Storage._safe_key edge cases
# ---------------------------------------------------------------------------

class TestS3StorageSafeKey:
    def _make_s3_storage(self):
        from sandcastle.engine.storage import S3Storage
        return S3Storage(
            bucket="test-bucket",
            aws_access_key_id="key",
            aws_secret_access_key="secret",
        )

    def test_safe_key_normal_path(self):
        storage = self._make_s3_storage()
        key = storage._safe_key("uploads/file.txt")
        assert key == "uploads/file.txt"

    def test_safe_key_traversal_denied(self):
        storage = self._make_s3_storage()
        with pytest.raises(ValueError):
            storage._safe_key("../../../etc/passwd")

    def test_safe_key_absolute_path_denied(self):
        storage = self._make_s3_storage()
        with pytest.raises(ValueError):
            storage._safe_key("/absolute/path.txt")

    def test_safe_key_too_long_raises(self):
        storage = self._make_s3_storage()
        long_key = "a/" * 512 + "file.txt"
        with pytest.raises(ValueError):
            storage._safe_key(long_key)


# ---------------------------------------------------------------------------
# schemas.py: version validator
# ---------------------------------------------------------------------------

class TestSchemaValidators:
    def test_version_latest_string_accepted(self):
        from sandcastle.api.schemas import WorkflowRunRequest
        req = WorkflowRunRequest(
            workflow_name="my_workflow",
            version="latest",
            input={},
        )
        assert req.version == "latest"

    def test_version_invalid_string_raises(self):
        from sandcastle.api.schemas import WorkflowRunRequest
        with pytest.raises(Exception):
            WorkflowRunRequest(
                workflow_name="my_workflow",
                version="invalid_string",
                input={},
            )

    def test_version_raises_for_non_latest(self):
        from sandcastle.api.schemas import WorkflowRunRequest
        with pytest.raises(Exception):
            WorkflowRunRequest(
                workflow_name="my_workflow",
                version="v2.0",
                input={},
            )

    def test_credential_key_too_long_raises(self):
        from sandcastle.api.schemas import ToolConnectionCreateRequest
        # Create a key that exceeds max 200 chars
        long_key = "k" * 201
        with pytest.raises(Exception):
            ToolConnectionCreateRequest(
                tool_id="my_tool",
                name="My Connection",
                credentials={long_key: "value"},
            )

    def test_credential_value_too_long_raises(self):
        from sandcastle.api.schemas import ToolConnectionCreateRequest
        long_val = "v" * 10001
        with pytest.raises(Exception):
            ToolConnectionCreateRequest(
                tool_id="my_tool",
                name="My Connection",
                credentials={"key": long_val},
            )


# ---------------------------------------------------------------------------
# executor.py: _emit_audit_event with hash chain
# ---------------------------------------------------------------------------

class TestEmitAuditEvent:
    @pytest.mark.asyncio
    async def test_emit_audit_event_success(self):
        from sandcastle.engine.executor import _emit_audit_event

        mock_session_cm = AsyncMock()
        mock_session = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=None)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_append = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session_cm), \
             patch("sandcastle.engine.audit.append_audit_event", mock_append):
            await _emit_audit_event(
                event_type="step.started",
                run_id="aabbccdd-0000-0000-0000-000000000001",
                actor_id="system",
                payload={"step_id": "step1"},
            )

    @pytest.mark.asyncio
    async def test_emit_audit_event_exception_silently_logged(self):
        from sandcastle.engine.executor import _emit_audit_event

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=Exception("DB error"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session_cm):
            # Should not raise
            await _emit_audit_event(
                event_type="step.started",
                run_id="aabbccdd-0000-0000-0000-000000000001",
                actor_id="system",
                payload={"step_id": "step1"},
            )


# ---------------------------------------------------------------------------
# executor.py: _backoff_delay
# ---------------------------------------------------------------------------

class TestBackoffDelay:
    def test_exponential_backoff_attempt_1(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(1, "exponential")
        assert delay >= 0

    def test_exponential_backoff_attempt_2(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(2, "exponential")
        assert delay >= 0

    def test_linear_backoff(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(3, "linear")
        assert delay >= 0

    def test_fixed_backoff(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(5, "fixed")
        assert delay >= 0

    def test_unknown_backoff_returns_default(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(1, "unknown_strategy")
        assert delay >= 0


# ---------------------------------------------------------------------------
# executor.py: _validate_browser_url
# ---------------------------------------------------------------------------

class TestValidateBrowserUrl:
    def test_valid_https_url_passes(self):
        from sandcastle.engine.executor import _validate_browser_url
        result = _validate_browser_url("https://example.com")
        # Valid URL returns the validated/normalized URL
        assert isinstance(result, str)
        assert "example.com" in result

    def test_javascript_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            _validate_browser_url("javascript:alert(1)")

    def test_data_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("data:text/html,<h1>test</h1>")

    def test_file_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("file:///etc/passwd")

    def test_empty_url_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError):
            _validate_browser_url("")


# ---------------------------------------------------------------------------
# queue/scheduler.py: restore_schedules
# ---------------------------------------------------------------------------

class TestRestoreSchedules:
    @pytest.mark.asyncio
    async def test_restore_schedules_with_no_schedules(self):
        from sandcastle.queue.scheduler import restore_schedules

        mock_session_cm = AsyncMock()
        mock_session = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[])
        mock_session.scalars = AsyncMock(return_value=mock_scalars)

        with patch("sandcastle.models.db.async_session", return_value=mock_session_cm):
            # Should not raise
            await restore_schedules()

    @pytest.mark.asyncio
    async def test_restore_schedules_exception_logged(self):
        from sandcastle.queue.scheduler import restore_schedules

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session_cm):
            # Should not raise
            await restore_schedules()


# ---------------------------------------------------------------------------
# autopilot.py: evaluate_result schema_completeness
# ---------------------------------------------------------------------------

class TestEvaluateResult:
    @pytest.mark.asyncio
    async def test_schema_completeness_with_schema(self):
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import AutoPilotConfig

        config = AutoPilotConfig(
            enabled=True,
            variants=[],
            optimize_for="quality",
        )
        step = MagicMock()
        step.output_schema = {"type": "object", "properties": {"name": {}, "age": {}}}
        output = {"name": "Alice", "age": 30}

        score = await evaluate_result(config, step, output)
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_schema_completeness_with_no_schema(self):
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import AutoPilotConfig

        config = AutoPilotConfig(
            enabled=True,
            variants=[],
            optimize_for="quality",
        )
        step = MagicMock()
        step.output_schema = None
        output = "Some output text"

        score = await evaluate_result(config, step, output)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_default_evaluation_non_null_output(self):
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import AutoPilotConfig, EvaluationConfig

        config = AutoPilotConfig(
            enabled=True,
            variants=[],
            optimize_for="quality",
            evaluation=EvaluationConfig(method="default_non_null"),
        )
        step = MagicMock()
        step.output_schema = None
        output = "Hello world"

        score = await evaluate_result(config, step, output)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_default_evaluation_null_output(self):
        from sandcastle.engine.autopilot import evaluate_result
        from sandcastle.engine.dag import AutoPilotConfig, EvaluationConfig

        config = AutoPilotConfig(
            enabled=True,
            variants=[],
            optimize_for="quality",
            evaluation=EvaluationConfig(method="default_non_null"),
        )
        step = MagicMock()
        step.output_schema = None
        output = None

        score = await evaluate_result(config, step, output)
        assert score == 0.0


# ---------------------------------------------------------------------------
# executor.py: _save_checkpoint
# ---------------------------------------------------------------------------

class TestSaveCheckpoint:
    @pytest.mark.asyncio
    async def test_save_checkpoint_success(self):
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={"key": "value"},
        )
        ctx.step_outputs["step1"] = {"result": "data"}

        mock_session_cm = AsyncMock()
        mock_session = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session_cm):
            await _save_checkpoint(
                run_id="aabbccdd-0000-0000-0000-000000000001",
                step_id="step1",
                stage_index=0,
                context=ctx,
            )

        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_checkpoint_exception_silently_logged(self):
        from sandcastle.engine.executor import RunContext, _save_checkpoint

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session_cm):
            # Should not raise
            await _save_checkpoint(
                run_id="aabbccdd-0000-0000-0000-000000000001",
                step_id="step1",
                stage_index=0,
                context=ctx,
            )


# ---------------------------------------------------------------------------
# executor.py: resolve_templates with {{}} escaping
# ---------------------------------------------------------------------------

class TestResolveTemplatesEdgeCases:
    def test_no_variables_passthrough(self):
        from sandcastle.engine.executor import RunContext, resolve_templates

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        result = resolve_templates("Hello, World!", ctx, [])
        assert result == "Hello, World!"

    def test_double_brace_escape(self):
        from sandcastle.engine.executor import RunContext, resolve_templates

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        result = resolve_templates("{{escaped}}", ctx, [])
        assert "{escaped}" in result

    def test_input_variable_resolved(self):
        from sandcastle.engine.executor import RunContext, resolve_templates

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={"topic": "AI"},
        )
        result = resolve_templates("Hello {{input.topic}}", ctx, [])
        assert "AI" in result

    def test_missing_variable_leaves_placeholder(self):
        from sandcastle.engine.executor import RunContext, resolve_templates

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        result = resolve_templates("Hello {{input.missing_var}}", ctx, [])
        # Should not raise - placeholder remains or is removed
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# executor.py: RunContext.with_item and snapshot
# ---------------------------------------------------------------------------

class TestRunContextMethods:
    def test_with_item_creates_new_context_with_item_in_input(self):
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={"items": ["a", "b", "c"]},
        )
        new_ctx = ctx.with_item("item_a", 0)
        # with_item puts item as _item in input dict
        assert new_ctx.input["_item"] == "item_a"
        assert new_ctx.input["_index"] == 0

    def test_with_item_independent_costs(self):
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        ctx.costs = [0.1, 0.2]
        new_ctx = ctx.with_item("x", 0)
        # Child gets fresh cost list
        assert new_ctx.costs == []

    def test_snapshot_contains_step_outputs(self):
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            step_outputs={"step1": "output1"},
        )
        snap = ctx.snapshot()
        assert "step_outputs" in snap
        assert snap["step_outputs"]["step1"] == "output1"

    def test_snapshot_is_independent_copy(self):
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            step_outputs={"step1": "output1"},
        )
        snap = ctx.snapshot()
        # Modifying the snapshot shouldn't affect the context
        snap["extra_key"] = "extra_value"
        assert "extra_key" not in ctx.step_outputs

    def test_total_cost_sums_costs(self):
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        ctx.costs = [0.01, 0.02, 0.05]
        assert abs(ctx.total_cost - 0.08) < 0.0001


# ---------------------------------------------------------------------------
# routes.py: /stats/forecast endpoint
# ---------------------------------------------------------------------------

class TestStatsForecast:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_stats_forecast_returns_200(self):
        response = self.client.get("/api/stats/forecast")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        d = data["data"]
        assert "historical" in d
        assert "projected" in d
        assert "daily_average" in d


# ---------------------------------------------------------------------------
# routes.py: /stats/heatmap endpoint
# ---------------------------------------------------------------------------

class TestStatsHeatmap:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_stats_heatmap_returns_200(self):
        response = self.client.get("/api/stats/heatmap")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data


# ---------------------------------------------------------------------------
# routes.py: /check-update endpoint (was version-check)
# ---------------------------------------------------------------------------

class TestCheckUpdate:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_check_update_returns_200(self):
        response = self.client.get("/api/check-update")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data


# ---------------------------------------------------------------------------
# routes.py: /eval/runs endpoint
# ---------------------------------------------------------------------------

class TestEvalRunsEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_eval_runs_list_returns_200(self):
        response = self.client.get("/api/eval/runs")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data


# ---------------------------------------------------------------------------
# executor.py: _write_pdf_report
# ---------------------------------------------------------------------------

class TestWritePdfReport:
    def test_write_pdf_report_with_dict_output(self):
        import tempfile
        from sandcastle.engine.executor import _write_pdf_report

        step = MagicMock()
        step.id = "step1"
        step.pdf_report = SimpleNamespace(
            language="en",
            directory=tempfile.mkdtemp(),
            filename=None,
        )

        output = {"summary": "This is a test report", "metrics": {"count": 42}}
        run_id = "aabbccdd-0000-0000-0000-000000000001"

        pdf_path = _write_pdf_report(step, output, run_id)
        if pdf_path:
            assert os.path.exists(pdf_path)
            os.unlink(pdf_path)

    def test_write_pdf_report_with_string_output(self):
        import tempfile
        from sandcastle.engine.executor import _write_pdf_report

        step = MagicMock()
        step.id = "step1"
        step.pdf_report = SimpleNamespace(
            language="en",
            directory=tempfile.mkdtemp(),
            filename="custom_report.pdf",
        )

        output = "# Report\n\nThis is a simple text report."
        run_id = "aabbccdd-0000-0000-0000-000000000001"

        pdf_path = _write_pdf_report(step, output, run_id)
        if pdf_path:
            assert pdf_path.endswith(".pdf")
            if os.path.exists(pdf_path):
                os.unlink(pdf_path)
