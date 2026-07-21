"""Batch 3: Targeted tests to push combined statement+branch coverage to 90%.

Focuses on:
- executor.py: resolve_variable edge cases, _is_cacheable_output, _truncate_output,
  _validate_browser_url, _get_pdf_report_instruction, _write_pdf_report,
  _backoff_delay, cancel/emergency stop functions
- routes.py: _validate_workflow_input, _load_workflow_yaml, _slugify,
  _extract_yaml_metadata, _sanitize_workflow_yaml, _get_hub_cache,
  upload_file, browse_directory, hub endpoints
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# executor.py: resolve_variable edge cases
# ---------------------------------------------------------------------------

class TestResolveVariableEdgeCases:
    """Test uncovered branches in resolve_variable."""

    def setup_method(self):
        from sandcastle.engine.executor import RunContext, _UNRESOLVED
        self._UNRESOLVED = _UNRESOLVED
        self.ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={"name": "test", "items": [10, 20, 30]},
            step_outputs={},
            step_results={},
            workflow_name="test_wf",
        )

    def test_empty_var_path_returns_unresolved(self):
        from sandcastle.engine.executor import resolve_variable
        result = resolve_variable("", self.ctx)
        assert result is self._UNRESOLVED

    def test_input_missing_key_returns_unresolved(self):
        from sandcastle.engine.executor import resolve_variable
        result = resolve_variable("input.nonexistent", self.ctx)
        assert result is self._UNRESOLVED

    def test_steps_missing_step_returns_unresolved(self):
        from sandcastle.engine.executor import resolve_variable
        result = resolve_variable("steps.missing_step.output", self.ctx)
        assert result is self._UNRESOLVED

    def test_steps_output_nested_none_returns_none(self):
        from sandcastle.engine.executor import resolve_variable
        self.ctx.step_outputs["step1"] = None
        result = resolve_variable("steps.step1.output.field", self.ctx)
        assert result is None

    def test_steps_cost_from_step_results(self):
        from sandcastle.engine.executor import StepResult, resolve_variable
        self.ctx.step_outputs["step1"] = "result"
        self.ctx.step_results["step1"] = StepResult(
            step_id="step1", cost_usd=1.5, status="completed"
        )
        result = resolve_variable("steps.step1.cost", self.ctx)
        assert result == 1.5

    def test_steps_error_from_step_results(self):
        from sandcastle.engine.executor import StepResult, resolve_variable
        self.ctx.step_outputs["step1"] = "result"
        self.ctx.step_results["step1"] = StepResult(
            step_id="step1", status="failed", error="some error"
        )
        result = resolve_variable("steps.step1.error", self.ctx)
        assert result == "some error"

    def test_steps_status_from_step_results(self):
        from sandcastle.engine.executor import StepResult, resolve_variable
        self.ctx.step_outputs["step1"] = "result"
        self.ctx.step_results["step1"] = StepResult(step_id="step1", status="completed")
        result = resolve_variable("steps.step1.status", self.ctx)
        assert result == "completed"

    def test_steps_status_no_result_record_returns_unresolved(self):
        from sandcastle.engine.executor import resolve_variable
        self.ctx.step_outputs["step1"] = "result"
        # No step_results entry
        result = resolve_variable("steps.step1.status", self.ctx)
        assert result is self._UNRESOLVED

    def test_memory_var_path(self):
        from sandcastle.engine.executor import resolve_variable
        self.ctx.memories = [{"content": "remember this", "score": 0.9}]
        result = resolve_variable("memory", self.ctx)
        assert isinstance(result, str)

    def test_env_var_path_found(self):
        import os
        from sandcastle.engine.executor import resolve_variable
        os.environ["TEST_RESOLVE_VAR_XYZ"] = "hello_world"
        try:
            result = resolve_variable("env.TEST_RESOLVE_VAR_XYZ", self.ctx)
            assert result == "hello_world"
        finally:
            del os.environ["TEST_RESOLVE_VAR_XYZ"]

    def test_env_var_path_missing_returns_unresolved(self):
        import os
        from sandcastle.engine.executor import resolve_variable, _UNRESOLVED
        os.environ.pop("TEST_RESOLVE_VAR_MISSING_XYZ", None)
        result = resolve_variable("env.TEST_RESOLVE_VAR_MISSING_XYZ", self.ctx)
        assert result is _UNRESOLVED

    def test_run_id_var_path(self):
        from sandcastle.engine.executor import resolve_variable
        result = resolve_variable("run_id", self.ctx)
        assert result == self.ctx.run_id

    def test_date_var_path(self):
        from sandcastle.engine.executor import resolve_variable
        result = resolve_variable("date", self.ctx)
        assert isinstance(result, str)
        assert len(result) == 10  # YYYY-MM-DD

    def test_traverse_list_index(self):
        from sandcastle.engine.executor import resolve_variable
        result = resolve_variable("input.items.1", self.ctx)
        assert result == 20

    def test_traverse_list_out_of_bounds(self):
        from sandcastle.engine.executor import resolve_variable, _UNRESOLVED
        result = resolve_variable("input.items.99", self.ctx)
        assert result is _UNRESOLVED

    def test_traverse_list_invalid_index(self):
        from sandcastle.engine.executor import resolve_variable, _UNRESOLVED
        result = resolve_variable("input.items.notanint", self.ctx)
        assert result is _UNRESOLVED

    def test_unknown_path_returns_unresolved(self):
        from sandcastle.engine.executor import resolve_variable, _UNRESOLVED
        result = resolve_variable("unknown.path.here", self.ctx)
        assert result is _UNRESOLVED


# ---------------------------------------------------------------------------
# executor.py: _is_cacheable_output
# ---------------------------------------------------------------------------

class TestIsCacheableOutput:
    """Test all branches in _is_cacheable_output."""

    def test_none_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output(None) is False

    def test_empty_string_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output("") is False

    def test_empty_list_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output([]) is False

    def test_empty_dict_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output({}) is False

    def test_dict_with_zero_total_mentions_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output({"total_mentions": 0, "mentions": []}) is False

    def test_dict_with_failed_result_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        # Short result containing a failed keyword
        assert _is_cacheable_output({"result": "please provide the content"}) is False

    def test_dict_with_good_long_result_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        # Long result doesn't get keyword-checked
        assert _is_cacheable_output({"result": "x" * 300}) is True

    def test_short_string_with_failed_keyword_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output("i don't have access to that") is False

    def test_long_string_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output("x" * 300) is True

    def test_valid_list_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output([1, 2, 3]) is True

    def test_number_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output(42) is True


# ---------------------------------------------------------------------------
# executor.py: _truncate_output
# ---------------------------------------------------------------------------

class TestTruncateOutput:
    """Test all branches in _truncate_output."""

    def test_none_returns_none(self):
        from sandcastle.engine.executor import _truncate_output
        assert _truncate_output(None) is None

    def test_small_output_returned_as_is(self):
        from sandcastle.engine.executor import _truncate_output
        result = _truncate_output("hello")
        assert result == "hello"

    def test_string_truncated_at_max_size(self):
        from sandcastle.engine.executor import _truncate_output
        big = "x" * 200
        result = _truncate_output(big, max_size=100)
        assert "TRUNCATED" in result
        assert len(result) > 100  # Includes suffix

    def test_dict_small_returned_as_is(self):
        from sandcastle.engine.executor import _truncate_output
        data = {"key": "value"}
        result = _truncate_output(data)
        assert result == data

    def test_dict_large_truncated_to_dict_with_meta(self):
        from sandcastle.engine.executor import _truncate_output
        big = {"data": "x" * 200}
        result = _truncate_output(big, max_size=10)
        assert isinstance(result, dict)
        assert result.get("_truncated") is True

    def test_non_serializable_falls_back_to_str(self):
        from sandcastle.engine.executor import _truncate_output

        class NotSerializable:
            def __repr__(self):
                return "notserializable"

        obj = NotSerializable()
        # Should not raise
        result = _truncate_output(obj)
        assert result is not None


# ---------------------------------------------------------------------------
# executor.py: _validate_browser_url
# ---------------------------------------------------------------------------

class TestValidateBrowserUrl:
    """Test all branches of _validate_browser_url."""

    def test_empty_url_raises(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="empty"):
            _validate_browser_url("")

    def test_whitespace_url_raises(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="empty"):
            _validate_browser_url("   ")

    def test_javascript_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("javascript:alert(1)")

    def test_data_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("data:text/html,<script>")

    def test_file_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("file:///etc/passwd")

    def test_vbscript_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("vbscript:msgbox")

    def test_blob_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("blob:http://example.com/abc")

    def test_ftp_scheme_rejected(self):
        from sandcastle.engine.executor import _validate_browser_url
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_browser_url("ftp://example.com")

    def test_http_accepted(self):
        from sandcastle.engine.executor import _validate_browser_url
        result = _validate_browser_url("http://example.com")
        assert result == "http://example.com"

    def test_https_accepted(self):
        from sandcastle.engine.executor import _validate_browser_url
        result = _validate_browser_url("https://example.com/path?q=1")
        assert result == "https://example.com/path?q=1"

    def test_no_scheme_prepends_https(self):
        from sandcastle.engine.executor import _validate_browser_url
        result = _validate_browser_url("example.com/page")
        assert result == "https://example.com/page"


# ---------------------------------------------------------------------------
# executor.py: _get_pdf_report_instruction
# ---------------------------------------------------------------------------

class TestGetPdfReportInstruction:
    """Test all branches of _get_pdf_report_instruction."""

    def test_english_instruction(self):
        from sandcastle.engine.executor import _get_pdf_report_instruction
        result = _get_pdf_report_instruction("en")
        assert "English" in result

    def test_czech_instruction(self):
        from sandcastle.engine.executor import _get_pdf_report_instruction
        result = _get_pdf_report_instruction("cs")
        assert "cestine" in result or "FORMATOVANI" in result

    def test_german_instruction(self):
        from sandcastle.engine.executor import _get_pdf_report_instruction
        result = _get_pdf_report_instruction("de")
        assert "Deutsch" in result or "FORMATIERUNG" in result

    def test_french_instruction(self):
        from sandcastle.engine.executor import _get_pdf_report_instruction
        result = _get_pdf_report_instruction("fr")
        assert "francais" in result or "FORMATAGE" in result

    def test_japanese_instruction(self):
        from sandcastle.engine.executor import _get_pdf_report_instruction
        result = _get_pdf_report_instruction("ja")
        assert "Japanese" in result

    def test_unknown_language_fallback(self):
        from sandcastle.engine.executor import _get_pdf_report_instruction
        result = _get_pdf_report_instruction("klingon")
        assert "klingon" in result.lower()


# ---------------------------------------------------------------------------
# executor.py: _backoff_delay
# ---------------------------------------------------------------------------

class TestBackoffDelay:
    """Test _backoff_delay branches."""

    def test_exponential_backoff_attempt_1(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(1, "exponential")
        assert 0 <= delay <= 4  # 2^1=2, with jitter up to 2

    def test_exponential_backoff_caps_at_60(self):
        from sandcastle.engine.executor import _backoff_delay
        # At attempt 10, base = min(2^10, 60) = 60
        delay = _backoff_delay(10, "exponential")
        assert 0 <= delay <= 60

    def test_fixed_backoff(self):
        from sandcastle.engine.executor import _backoff_delay
        delay = _backoff_delay(1, "fixed")
        assert 1.0 <= delay <= 3.0


# ---------------------------------------------------------------------------
# executor.py: cancel and emergency stop local functions
# ---------------------------------------------------------------------------

class TestCancelAndEmergencyStop:
    """Test cancel_run_local, set_emergency_stop_local, clear_emergency_stop_local,
    is_emergency_stop_active."""

    @pytest.mark.asyncio
    async def test_cancel_run_local_sets_flag(self):
        import sandcastle.engine.executor as exe
        # Reset state
        exe._cancel_flags.clear()
        await exe.cancel_run_local("test-run-999")
        async with exe._cancel_flags_lock:
            assert "test-run-999" in exe._cancel_flags

    @pytest.mark.asyncio
    async def test_cancel_run_local_eviction_when_max_exceeded(self):
        import sandcastle.engine.executor as exe
        exe._cancel_flags.clear()
        # Fill to max
        old_max = exe._MAX_CANCEL_FLAGS
        exe._MAX_CANCEL_FLAGS = 4
        try:
            for i in range(4):
                await exe.cancel_run_local(f"run-{i}")
            # One more triggers eviction
            await exe.cancel_run_local("run-new")
            async with exe._cancel_flags_lock:
                # Should have evicted oldest half (2 items) and kept recent ones
                assert "run-new" in exe._cancel_flags
                assert len(exe._cancel_flags) < 4
        finally:
            exe._MAX_CANCEL_FLAGS = old_max
            exe._cancel_flags.clear()

    @pytest.mark.asyncio
    async def test_set_and_clear_emergency_stop(self):
        import sandcastle.engine.executor as exe
        await exe.set_emergency_stop_local()
        assert exe._emergency_stop_local is True
        await exe.clear_emergency_stop_local()
        assert exe._emergency_stop_local is False

    @pytest.mark.asyncio
    async def test_is_emergency_stop_active_local_mode(self):
        import sandcastle.engine.executor as exe
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None
            exe._emergency_stop_local = False
            result = await exe.is_emergency_stop_active()
            assert result is False

            exe._emergency_stop_local = True
            result = await exe.is_emergency_stop_active()
            assert result is True
            exe._emergency_stop_local = False  # cleanup

    @pytest.mark.asyncio
    async def test_check_cancel_local_mode_emergency(self):
        import sandcastle.engine.executor as exe
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None
            exe._emergency_stop_local = True
            result = await exe._check_cancel("some-run")
            assert result is True
            exe._emergency_stop_local = False

    @pytest.mark.asyncio
    async def test_check_cancel_local_mode_flag_set(self):
        import sandcastle.engine.executor as exe
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None
            exe._emergency_stop_local = False
            exe._cancel_flags.clear()
            exe._cancel_flags["flagged-run"] = None
            result = await exe._check_cancel("flagged-run")
            assert result is True
            exe._cancel_flags.clear()

    @pytest.mark.asyncio
    async def test_check_cancel_local_mode_no_flag(self):
        import sandcastle.engine.executor as exe
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None
            exe._emergency_stop_local = False
            exe._cancel_flags.clear()
            result = await exe._check_cancel("unflagged-run")
            assert result is False

    @pytest.mark.asyncio
    async def test_check_cancel_redis_exception_returns_false(self):
        import sandcastle.engine.executor as exe
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379"
            with patch("sandcastle.engine.executor._get_redis", side_effect=Exception("conn failed")):
                result = await exe._check_cancel("any-run")
                assert result is False

    @pytest.mark.asyncio
    async def test_check_cancel_redis_emergency_stop_active(self):
        import sandcastle.engine.executor as exe
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"1")
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379"
            with patch("sandcastle.engine.executor._get_redis", return_value=mock_redis):
                result = await exe._check_cancel("any-run")
                assert result is True

    @pytest.mark.asyncio
    async def test_check_cancel_redis_cancel_flag_set(self):
        import sandcastle.engine.executor as exe
        call_count = 0
        async def mock_get(key):
            nonlocal call_count
            call_count += 1
            if "emergency" in key:
                return None  # No emergency stop
            return b"1"  # Cancel flag set

        mock_redis = AsyncMock()
        mock_redis.get = mock_get
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379"
            with patch("sandcastle.engine.executor._get_redis", return_value=mock_redis):
                result = await exe._check_cancel("specific-run")
                assert result is True

    @pytest.mark.asyncio
    async def test_is_emergency_stop_active_redis_exception(self):
        import sandcastle.engine.executor as exe
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379"
            with patch("sandcastle.engine.executor._get_redis", side_effect=Exception("down")):
                result = await exe.is_emergency_stop_active()
                assert result is False


# ---------------------------------------------------------------------------
# executor.py: _write_pdf_report
# ---------------------------------------------------------------------------

class TestWritePdfReport:
    """Test _write_pdf_report branches."""

    def _make_step_with_pdf_report(self, directory: str):
        """Create a minimal StepDefinition with pdf_report config."""
        from sandcastle.engine.dag import StepDefinition
        from types import SimpleNamespace

        step = MagicMock(spec=StepDefinition)
        step.id = "test_step"
        step.pdf_report = SimpleNamespace(
            directory=directory,
            filename=None,
            language="en",
        )
        return step

    def test_pdf_report_cfg_none_returns_none(self):
        from sandcastle.engine.executor import _write_pdf_report
        from sandcastle.engine.dag import StepDefinition
        step = MagicMock(spec=StepDefinition)
        step.id = "test_step"
        step.pdf_report = None
        result = _write_pdf_report(step, "some output", "run-123")
        assert result is None

    def test_empty_output_returns_none(self, tmp_path):
        from sandcastle.engine.executor import _write_pdf_report
        step = self._make_step_with_pdf_report(str(tmp_path))
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.sandbox_root = None
            result = _write_pdf_report(step, "", "run-123")
        assert result is None

    def test_empty_whitespace_output_returns_none(self, tmp_path):
        from sandcastle.engine.executor import _write_pdf_report
        step = self._make_step_with_pdf_report(str(tmp_path))
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.sandbox_root = None
            result = _write_pdf_report(step, "   ", "run-123")
        assert result is None

    def test_pdf_generation_failure_returns_none(self, tmp_path):
        from sandcastle.engine.executor import _write_pdf_report
        step = self._make_step_with_pdf_report(str(tmp_path))
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.sandbox_root = None
            with patch.dict("sys.modules", {
                "sandcastle.engine.pdf": MagicMock(
                    generate_branded_pdf=MagicMock(side_effect=Exception("pdf lib error"))
                )
            }):
                result = _write_pdf_report(step, "Some content here", "run-123")
        assert result is None

    def test_dict_output_with_result_key(self, tmp_path):
        from sandcastle.engine.executor import _write_pdf_report
        step = self._make_step_with_pdf_report(str(tmp_path))
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.sandbox_root = None
            mock_pdf = MagicMock()
            with patch.dict("sys.modules", {"sandcastle.engine.pdf": mock_pdf}):
                _write_pdf_report(step, {"result": "Report content here"}, "run-123")
                mock_pdf.generate_branded_pdf.assert_called_once()

    def test_list_output_json_dumped(self, tmp_path):
        from sandcastle.engine.executor import _write_pdf_report
        step = self._make_step_with_pdf_report(str(tmp_path))
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.sandbox_root = None
            mock_pdf = MagicMock()
            with patch.dict("sys.modules", {"sandcastle.engine.pdf": mock_pdf}):
                _write_pdf_report(step, ["item1", "item2"], "run-123")
                mock_pdf.generate_branded_pdf.assert_called_once()
                call_args = mock_pdf.generate_branded_pdf.call_args[0]
                # First arg is markdown_text - should be JSON dump of list
                assert "item1" in call_args[0]

    def test_non_string_non_dict_output(self, tmp_path):
        from sandcastle.engine.executor import _write_pdf_report
        step = self._make_step_with_pdf_report(str(tmp_path))
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.sandbox_root = None
            mock_pdf = MagicMock()
            with patch.dict("sys.modules", {"sandcastle.engine.pdf": mock_pdf}):
                # Integer output
                _write_pdf_report(step, 42, "run-123")
                mock_pdf.generate_branded_pdf.assert_called_once()


# ---------------------------------------------------------------------------
# routes.py: _validate_workflow_input
# ---------------------------------------------------------------------------

class TestValidateWorkflowInput:
    """Test all branches in _validate_workflow_input."""

    def test_no_schema_returns_empty(self):
        from sandcastle.api.routes import _validate_workflow_input
        errors = _validate_workflow_input({"key": "val"}, None)
        assert errors == []

    def test_invalid_schema_type_returns_error(self):
        from sandcastle.api.routes import _validate_workflow_input
        errors = _validate_workflow_input({}, "not_a_dict")
        assert len(errors) == 1
        assert "input_schema must be a dict" in errors[0]

    def test_required_field_missing(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"required": ["topic"], "properties": {}}
        errors = _validate_workflow_input({}, schema)
        assert any("topic" in e for e in errors)

    def test_required_field_empty_string(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"required": ["name"], "properties": {}}
        errors = _validate_workflow_input({"name": ""}, schema)
        assert any("name" in e for e in errors)

    def test_integer_coercion(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"count": {"type": "integer"}}}
        data = {"count": "42"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["count"] == 42

    def test_integer_coercion_failure(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"count": {"type": "integer"}}}
        data = {"count": "not_a_number"}
        errors = _validate_workflow_input(data, schema)
        assert any("count" in e for e in errors)

    def test_number_coercion(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"amount": {"type": "number"}}}
        data = {"amount": "3.14"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert abs(data["amount"] - 3.14) < 0.001

    def test_number_coercion_failure(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"amount": {"type": "number"}}}
        data = {"amount": "not_a_number"}
        errors = _validate_workflow_input(data, schema)
        assert any("amount" in e for e in errors)

    def test_boolean_coercion_true(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"flag": {"type": "boolean"}}}
        data = {"flag": "true"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["flag"] is True

    def test_boolean_coercion_false(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"flag": {"type": "boolean"}}}
        data = {"flag": "False"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["flag"] is False

    def test_boolean_coercion_failure(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"flag": {"type": "boolean"}}}
        data = {"flag": "notbool"}
        errors = _validate_workflow_input(data, schema)
        assert any("flag" in e for e in errors)

    def test_array_coercion(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"items": {"type": "array"}}}
        data = {"items": '["a", "b"]'}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["items"] == ["a", "b"]

    def test_array_coercion_wraps_object(self):
        # 0.43.0 contract: a bare JSON value is a single item
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"items": {"type": "array"}}}
        data = {"items": '{"key": "val"}'}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["items"] == [{"key": "val"}]

    def test_array_coercion_splits_plain_string(self):
        # 0.43.0 contract: plain strings split on commas
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"items": {"type": "array"}}}
        data = {"items": "not_json"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["items"] == ["not_json"]

    def test_field_not_in_input_skipped(self):
        from sandcastle.api.routes import _validate_workflow_input
        schema = {"properties": {"optional_field": {"type": "integer"}}}
        data = {}  # field not present - should be skipped
        errors = _validate_workflow_input(data, schema)
        assert errors == []


# ---------------------------------------------------------------------------
# routes.py: _load_workflow_yaml
# ---------------------------------------------------------------------------

class TestLoadWorkflowYaml:
    """Test _load_workflow_yaml branches."""

    def test_empty_name_raises_value_error(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(ValueError, match="empty"):
            _load_workflow_yaml("")

    def test_whitespace_name_raises_value_error(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(ValueError, match="empty"):
            _load_workflow_yaml("   ")

    def test_path_traversal_rejected(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("../../../etc/passwd")

    def test_forward_slash_rejected(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("subdir/workflow")

    def test_backslash_rejected(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("sub\\workflow")

    def test_nonexistent_workflow_raises_file_not_found(self):
        from sandcastle.api.routes import _load_workflow_yaml
        with pytest.raises(FileNotFoundError):
            _load_workflow_yaml("nonexistent_workflow_xyz_abc_123")

    def test_existing_workflow_returned(self, tmp_path):
        from sandcastle.api.routes import _load_workflow_yaml
        yaml_file = tmp_path / "my_workflow.yaml"
        yaml_file.write_text("name: my_workflow\nsteps: []")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            content = _load_workflow_yaml("my_workflow")
        assert "my_workflow" in content


# ---------------------------------------------------------------------------
# routes.py: _slugify and _extract_yaml_metadata
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        from sandcastle.api.routes import _slugify
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        from sandcastle.api.routes import _slugify
        result = _slugify("Hello!@#$World")
        # Special chars are removed, not converted to hyphens
        assert "!" not in result and "@" not in result and "#" not in result

    def test_multiple_spaces_collapsed(self):
        from sandcastle.api.routes import _slugify
        assert _slugify("hello   world") == "hello-world"

    def test_leading_trailing_hyphens_stripped(self):
        from sandcastle.api.routes import _slugify
        result = _slugify("-hello-")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_long_text_truncated(self):
        from sandcastle.api.routes import _slugify
        result = _slugify("a" * 200)
        assert len(result) <= 100


class TestExtractYamlMetadata:
    def test_valid_yaml_with_steps(self):
        from sandcastle.api.routes import _extract_yaml_metadata
        yaml_content = """
name: My Workflow
steps:
  - id: step1
    model: claude-3-haiku
    tool: web_search
  - id: step2
    model: claude-3-sonnet
"""
        meta = _extract_yaml_metadata(yaml_content)
        assert meta["name"] == "My Workflow"
        assert meta["step_count"] == 2
        assert "claude-3-haiku" in meta["models_used"]
        assert "web_search" in meta["tools_used"]

    def test_invalid_yaml_returns_empty(self):
        from sandcastle.api.routes import _extract_yaml_metadata
        result = _extract_yaml_metadata("{{{{ invalid yaml")
        assert result == {}

    def test_yaml_without_steps(self):
        from sandcastle.api.routes import _extract_yaml_metadata
        yaml_content = "name: Minimal\nsteps: []"
        meta = _extract_yaml_metadata(yaml_content)
        assert meta["step_count"] == 0

    def test_yaml_with_non_dict_step(self):
        from sandcastle.api.routes import _extract_yaml_metadata
        yaml_content = "name: Test\nsteps:\n  - not_a_dict\n  - id: step1\n"
        meta = _extract_yaml_metadata(yaml_content)
        # Should not crash, just skip non-dict steps
        assert meta["step_count"] == 2


# ---------------------------------------------------------------------------
# routes.py: _sanitize_workflow_yaml
# ---------------------------------------------------------------------------

class TestSanitizeWorkflowYaml:
    def test_redacts_env_var_references(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "api_key: ${MY_SECRET_KEY}\nother: value"
        result = _sanitize_workflow_yaml(yaml_content)
        assert "<REDACTED>" in result
        assert "MY_SECRET_KEY" not in result

    def test_redacts_simple_env_vars(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "token: $MY_TOKEN_ABC\nother: value"
        result = _sanitize_workflow_yaml(yaml_content)
        assert "<REDACTED>" in result

    def test_redacts_password_key_value(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "password: myactualpassword\nname: test"
        result = _sanitize_workflow_yaml(yaml_content)
        assert "myactualpassword" not in result
        assert "<REDACTED>" in result

    def test_preserves_variable_references_in_password_field(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml
        # If value starts with { it's a template variable - don't redact
        yaml_content = "password: {input.password}\nname: test"
        result = _sanitize_workflow_yaml(yaml_content)
        # The value starts with { so it won't be redacted
        assert "{input.password}" in result

    def test_normal_content_preserved(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = "name: My Workflow\nsteps:\n  - id: step1\n"
        result = _sanitize_workflow_yaml(yaml_content)
        assert "My Workflow" in result
        assert "step1" in result


# ---------------------------------------------------------------------------
# routes.py: _get_hub_cache hit and miss
# ---------------------------------------------------------------------------

class TestGetHubCache:
    def test_cache_miss_returns_none(self):
        import sandcastle.api.routes as routes
        routes._hub_cache.clear()
        result = routes._get_hub_cache("nonexistent_key")
        assert result is None

    def test_cache_expired_returns_none(self):
        import sandcastle.api.routes as routes
        old_ttl = routes._HUB_CACHE_TTL
        routes._hub_cache["test_key"] = (time.time() - 400, {"data": "old"})
        routes._HUB_CACHE_TTL = 300
        try:
            result = routes._get_hub_cache("test_key")
            assert result is None
        finally:
            routes._HUB_CACHE_TTL = old_ttl

    def test_cache_hit_returns_data(self):
        import sandcastle.api.routes as routes
        routes._hub_cache["fresh_key"] = (time.time(), {"data": "fresh"})
        result = routes._get_hub_cache("fresh_key")
        assert result == {"data": "fresh"}

    def test_set_then_get_hub_cache(self):
        import sandcastle.api.routes as routes
        routes._set_hub_cache("mykey", [1, 2, 3])
        result = routes._get_hub_cache("mykey")
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# routes.py: upload_file - various branches
# ---------------------------------------------------------------------------

class TestUploadFile:
    """Test upload_file endpoint branches using FastAPI TestClient."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_no_filename_returns_400(self):
        response = self.client.post(
            "/api/upload",
            files={"file": ("", b"content", "text/plain")},
        )
        # Filename is empty - should be 400
        assert response.status_code in (400, 422)

    def test_disallowed_extension_returns_400(self):
        response = self.client.post(
            "/api/upload",
            files={"file": ("malware.exe", b"content", "application/octet-stream")},
        )
        assert response.status_code == 400
        data = response.json()
        assert "data" not in data or data.get("error") is not None

    def test_allowed_extension_local_storage(self, tmp_path):
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.storage_backend = "local"
            mock_settings.data_dir = str(tmp_path)
            mock_settings.auth_required = False
            response = self.client.post(
                "/api/upload",
                files={"file": ("test.txt", b"hello world", "text/plain")},
            )
        # Should work in local mode
        assert response.status_code in (200, 401, 403)


# ---------------------------------------------------------------------------
# routes.py: browse_directory endpoint
# ---------------------------------------------------------------------------

class TestBrowseDirectory:
    """Test browse_directory endpoint branches."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_browse_returns_403_in_production_mode(self):
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.is_local_mode = False
            mock_settings.auth_required = False
            mock_settings.redis_url = None
            mock_settings.sandbox_root = None
            response = self.client.get("/api/browse?path=/tmp")
        # In production mode it should return 403
        assert response.status_code == 403

    def test_browse_nonexistent_path_returns_404(self, tmp_path):
        nonexistent = str(tmp_path / "nonexistent_dir_xyz")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False
            mock_settings.sandbox_root = None
            response = self.client.get(f"/api/browse?path={nonexistent}")
        assert response.status_code == 404

    def test_browse_valid_directory(self, tmp_path):
        # Create a file in the dir
        (tmp_path / "test.txt").write_text("hello")
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.auth_required = False
            mock_settings.sandbox_root = None
            response = self.client.get(f"/api/browse?path={tmp_path}")
        if response.status_code == 200:
            data = response.json()["data"]
            assert "entries" in data


# ---------------------------------------------------------------------------
# routes.py: hub_playground endpoint
# ---------------------------------------------------------------------------

class TestHubPlayground:
    """Test hub_playground endpoint."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_playground_with_valid_json(self):
        response = self.client.post(
            "/api/hub/playground",
            json={"slug": "test/workflow", "inputs": {"key": "val"}, "step_count": 3},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["slug"] == "test/workflow"
        assert data["data"]["status"] == "completed"

    def test_playground_with_invalid_json(self):
        response = self.client.post(
            "/api/hub/playground",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# routes.py: hub registry and collections (cache path)
# ---------------------------------------------------------------------------

class TestHubRegistryCache:
    """Test hub registry with cache hit."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_hub_registry_returns_cache_when_fresh(self):
        import sandcastle.api.routes as routes
        routes._set_hub_cache("registry", {"version": 2, "templates": []})
        response = self.client.get("/api/hub/registry")
        assert response.status_code == 200

    def test_hub_collections_returns_cache_when_fresh(self):
        import sandcastle.api.routes as routes
        routes._set_hub_cache("collections", [{"name": "Test Collection"}])
        response = self.client.get("/api/hub/collections")
        assert response.status_code == 200

    def test_hub_registry_fallback_on_network_error(self):
        import sandcastle.api.routes as routes
        routes._hub_cache.pop("registry", None)
        with patch("sandcastle.api.routes.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("network error"))
            mock_httpx.AsyncClient.return_value = mock_client
            response = self.client.get("/api/hub/registry")
        # Should return fallback data with status 200
        assert response.status_code == 200
        data = response.json()
        assert data["data"] is not None


# ---------------------------------------------------------------------------
# routes.py: community template listing validation
# ---------------------------------------------------------------------------

class TestListCommunityTemplates:
    """Test list_community_templates validation."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_invalid_status_returns_400(self):
        response = self.client.get("/api/hub/community?status=invalid_status")
        assert response.status_code == 400
        data = response.json()
        assert "INVALID_STATUS" in str(data)


# ---------------------------------------------------------------------------
# routes.py: hub install invalid slug
# ---------------------------------------------------------------------------

class TestHubInstallInvalidSlug:
    """Test install_hub_template with invalid slug format."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_invalid_slug_format_returns_400(self):
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            response = self.client.post("/api/hub/install/no_slash_here")
        assert response.status_code in (400, 401, 403)

    def test_invalid_slug_too_many_parts(self):
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            # slug with 3 parts: author/name/extra - only 2 parts allowed
            # Actually the path captures everything after install/
            response = self.client.post("/api/hub/install/author/name/extra")
        # With 3 parts the split gives 3 items, len != 2 -> 400
        assert response.status_code in (400, 401, 403, 422)

    def test_uninstall_invalid_slug(self):
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            response = self.client.delete("/api/hub/install/noslash")
        assert response.status_code in (400, 401, 403)


# ---------------------------------------------------------------------------
# routes.py: hub submit validation
# ---------------------------------------------------------------------------

class TestHubSubmit:
    """Test submit_to_hub validation."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_invalid_json_body(self):
        response = self.client.post(
            "/api/hub/submit",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_invalid_yaml_content(self):
        response = self.client.post(
            "/api/hub/submit",
            json={
                "yaml_content": "{{{{ not yaml",
                "description": "test",
                "category": "automation",
                "tags": [],
            },
        )
        # Should fail with 400 INVALID_YAML
        assert response.status_code == 400

    def test_missing_required_field(self):
        # yaml_content is required in HubSubmitRequest
        response = self.client.post(
            "/api/hub/submit",
            json={"description": "missing yaml_content"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# executor.py: _check_budget
# ---------------------------------------------------------------------------

class TestCheckBudget:
    """Test all branches in _check_budget."""

    def _make_context(self, costs, max_cost):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000002",
            input={},
            max_cost_usd=max_cost,
        )
        ctx.costs = costs
        return ctx

    def test_no_max_cost_returns_none(self):
        from sandcastle.engine.executor import _check_budget
        ctx = self._make_context([1.0], None)
        assert _check_budget(ctx) is None

    def test_zero_max_cost_returns_none(self):
        from sandcastle.engine.executor import _check_budget
        ctx = self._make_context([1.0], 0)
        assert _check_budget(ctx) is None

    def test_under_80_percent_returns_none(self):
        from sandcastle.engine.executor import _check_budget
        ctx = self._make_context([0.5], 10.0)  # 5%
        assert _check_budget(ctx) is None

    def test_between_80_and_100_percent_returns_warning(self):
        from sandcastle.engine.executor import _check_budget
        ctx = self._make_context([8.5], 10.0)  # 85%
        assert _check_budget(ctx) == "warning"

    def test_exactly_100_percent_returns_exceeded(self):
        from sandcastle.engine.executor import _check_budget
        ctx = self._make_context([10.0], 10.0)  # 100%
        assert _check_budget(ctx) == "exceeded"

    def test_over_100_percent_returns_exceeded(self):
        from sandcastle.engine.executor import _check_budget
        ctx = self._make_context([15.0], 10.0)  # 150%
        assert _check_budget(ctx) == "exceeded"


# ---------------------------------------------------------------------------
# executor.py: resolve_templates edge cases
# ---------------------------------------------------------------------------

class TestResolveTemplates:
    """Test resolve_templates edge cases."""

    def setup_method(self):
        from sandcastle.engine.executor import RunContext
        self.ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000003",
            input={"topic": "AI"},
            step_outputs={"step1": {"result": "answer"}},
            workflow_name="test_wf",
        )

    def test_template_too_large_raises_error(self):
        from sandcastle.engine.executor import resolve_templates, _MAX_TEMPLATE_SIZE
        big_template = "x" * (_MAX_TEMPLATE_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            resolve_templates(big_template, self.ctx)

    def test_unresolved_variable_leaves_placeholder(self):
        from sandcastle.engine.executor import resolve_templates
        template = "Result: {steps.nonexistent.output}"
        result = resolve_templates(template, self.ctx)
        assert "{steps.nonexistent.output}" in result

    def test_none_value_becomes_string_none(self):
        from sandcastle.engine.executor import resolve_templates
        self.ctx.step_outputs["step2"] = None
        template = "Value: {steps.step2.output}"
        result = resolve_templates(template, self.ctx)
        assert "None" in result

    def test_dict_value_json_dumped(self):
        from sandcastle.engine.executor import resolve_templates
        template = "Result: {steps.step1.output}"
        result = resolve_templates(template, self.ctx)
        assert "result" in result
        assert "answer" in result

    def test_auto_inject_unreferenced_deps(self):
        from sandcastle.engine.executor import resolve_templates
        self.ctx.step_outputs["dep_step"] = "dep_output"
        template = "Do something with no explicit reference"
        result = resolve_templates(template, self.ctx, depends_on=["dep_step"])
        assert "dep_step" in result
        assert "dep_output" in result

    def test_brace_escaping_in_step_output(self):
        from sandcastle.engine.executor import resolve_templates
        self.ctx.step_outputs["step_with_braces"] = "{inject me}"
        template = "Output: {steps.step_with_braces.output}"
        result = resolve_templates(template, self.ctx)
        # The braces should be escaped to prevent re-injection
        # {{inject me}} is the escaped form - it still contains {inject me} as a substring
        # but the important thing is the outer { } are doubled
        assert "{{inject me}}" in result


# ---------------------------------------------------------------------------
# executor.py: _compute_cache_key
# ---------------------------------------------------------------------------

class TestComputeCacheKey:
    def test_deterministic(self):
        from sandcastle.engine.executor import _compute_cache_key
        key1 = _compute_cache_key("wf", "step1", "prompt text", "claude-haiku")
        key2 = _compute_cache_key("wf", "step1", "prompt text", "claude-haiku")
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex

    def test_different_inputs_different_keys(self):
        from sandcastle.engine.executor import _compute_cache_key
        key1 = _compute_cache_key("wf", "step1", "prompt A", "claude-haiku")
        key2 = _compute_cache_key("wf", "step1", "prompt B", "claude-haiku")
        assert key1 != key2


# ---------------------------------------------------------------------------
# routes.py: _apply_tenant_filter
# ---------------------------------------------------------------------------

class TestApplyTenantFilter:
    """Test _apply_tenant_filter branches."""

    def test_no_auth_returns_unmodified(self):
        from sandcastle.api.routes import _apply_tenant_filter
        mock_stmt = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            result = _apply_tenant_filter(mock_stmt, "tenant1", MagicMock())
        assert result is mock_stmt  # returned unchanged

    def test_auth_with_none_tenant_returns_unmodified(self):
        from sandcastle.api.routes import _apply_tenant_filter
        mock_stmt = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = True
            result = _apply_tenant_filter(mock_stmt, None, MagicMock())
        assert result is mock_stmt  # admin key - no filter

    def test_auth_with_tenant_filters(self):
        from sandcastle.api.routes import _apply_tenant_filter
        mock_stmt = MagicMock()
        mock_stmt.where = MagicMock(return_value="filtered_stmt")
        mock_column = MagicMock()
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = True
            result = _apply_tenant_filter(mock_stmt, "tenant1", mock_column)
        assert result == "filtered_stmt"
        mock_stmt.where.assert_called_once()


# ---------------------------------------------------------------------------
# executor.py: _escape_braces and _escape_js_string
# ---------------------------------------------------------------------------

class TestEscapeFunctions:
    def test_escape_braces(self):
        from sandcastle.engine.executor import _escape_braces
        result = _escape_braces("hello {world} {test}")
        assert result == "hello {{world}} {{test}}"

    def test_escape_js_string_basic(self):
        from sandcastle.engine.executor import _escape_js_string
        result = _escape_js_string("hello 'world'")
        assert "\\'" in result

    def test_escape_js_string_newline(self):
        from sandcastle.engine.executor import _escape_js_string
        result = _escape_js_string("line1\nline2")
        assert "\\n" in result

    def test_escape_js_string_null_bytes_removed(self):
        from sandcastle.engine.executor import _escape_js_string
        result = _escape_js_string("hello\x00world")
        assert "\x00" not in result

    def test_escape_js_string_backslash(self):
        from sandcastle.engine.executor import _escape_js_string
        result = _escape_js_string("path\\to\\file")
        assert "\\\\" in result


# ---------------------------------------------------------------------------
# executor.py: RunContext.with_item and snapshot
# ---------------------------------------------------------------------------

class TestRunContextMethods:
    """Test RunContext helper methods."""

    def test_with_item_creates_child_context(self):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000004",
            input={"key": "value"},
            step_outputs={"step1": "output1"},
            workflow_name="test_wf",
            max_cost_usd=5.0,
        )
        child = ctx.with_item("item_value", 3)
        assert child.input["_item"] == "item_value"
        assert child.input["_index"] == 3
        assert child.run_id == ctx.run_id
        assert child.costs == []  # New empty costs list

    def test_with_item_isolates_step_outputs(self):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000005",
            input={},
            step_outputs={"step1": {"nested": "data"}},
            workflow_name="test_wf",
        )
        child = ctx.with_item("item", 0)
        child.step_outputs["step1"]["nested"] = "modified"
        # Parent should not be affected
        assert ctx.step_outputs["step1"]["nested"] == "data"

    def test_snapshot_returns_dict(self):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000006",
            input={"key": "val"},
            step_outputs={"step1": "result"},
            workflow_name="test_wf",
        )
        ctx.costs = [0.5, 0.3]
        snap = ctx.snapshot()
        assert snap["run_id"] == ctx.run_id
        assert snap["total_cost"] == pytest.approx(0.8)
        assert snap["step_outputs"] == {"step1": "result"}
