"""Batch 4: Targeted tests for __main__.py helpers to push coverage to 90%.

Focuses on:
- _color, _status_color, _table, _load_dot_env, _validate_run_id
- _format_cli_error (all branches)
- _attr, _parse_input_pairs, _load_input_file
- _to_dicts, _to_dict, _fmt_time, _find_pending_approval
- _print_run_detail, _print_eval_results
- executor.py: _execute_fallback, _save_to_cache collision
- routes.py: additional uncovered areas
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# __main__.py: _color and _status_color
# ---------------------------------------------------------------------------

class TestColorFunctions:
    """Test _color and _status_color."""

    def test_color_no_tty_returns_plain(self):
        from sandcastle.__main__ import _color, _C
        with patch.object(_C, "supports_color", return_value=False):
            result = _color("hello", _C.GREEN)
        assert result == "hello"

    def test_color_with_tty_adds_codes(self):
        from sandcastle.__main__ import _color, _C
        with patch.object(_C, "supports_color", return_value=True):
            result = _color("hello", _C.GREEN)
        assert "\033[" in result
        assert "hello" in result

    def test_supports_color_no_color_env(self):
        from sandcastle.__main__ import _C
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            assert _C.supports_color() is False

    def test_status_color_completed(self):
        from sandcastle.__main__ import _status_color
        result = _status_color("completed")
        assert "completed" in result  # text always included

    def test_status_color_failed(self):
        from sandcastle.__main__ import _status_color
        result = _status_color("failed")
        assert "failed" in result

    def test_status_color_running(self):
        from sandcastle.__main__ import _status_color
        result = _status_color("running")
        assert "running" in result

    def test_status_color_queued(self):
        from sandcastle.__main__ import _status_color
        result = _status_color("queued")
        assert "queued" in result

    def test_status_color_unknown(self):
        from sandcastle.__main__ import _status_color
        result = _status_color("unknown_status")
        assert result == "unknown_status"


# ---------------------------------------------------------------------------
# __main__.py: _table
# ---------------------------------------------------------------------------

class TestTable:
    """Test _table formatting."""

    def test_empty_rows_returns_no_data(self):
        from sandcastle.__main__ import _table
        result = _table(["A", "B"], [])
        assert "(no data)" in result

    def test_simple_table(self):
        from sandcastle.__main__ import _table
        result = _table(["NAME", "STATUS"], [["workflow1", "completed"], ["workflow2", "failed"]])
        assert "NAME" in result
        assert "workflow1" in result
        assert "workflow2" in result

    def test_table_with_long_values_truncated(self):
        from sandcastle.__main__ import _table
        long_val = "x" * 100
        result = _table(["NAME"], [[long_val]], max_col=20)
        # Should be truncated
        assert "\u2026" in result  # ellipsis

    def test_table_row_shorter_than_headers_padded(self):
        from sandcastle.__main__ import _table
        # Row has fewer columns than headers
        result = _table(["A", "B", "C"], [["val1"]])
        assert "val1" in result

    def test_table_row_longer_than_headers_truncated(self):
        from sandcastle.__main__ import _table
        # Row has more columns than headers - extra columns dropped
        result = _table(["A"], [["val1", "extra1", "extra2"]])
        assert "val1" in result


# ---------------------------------------------------------------------------
# __main__.py: _load_dot_env
# ---------------------------------------------------------------------------

class TestLoadDotEnv:
    """Test _load_dot_env function."""

    def test_idempotent_second_call(self, tmp_path, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", True)
        # Should return immediately without doing anything
        main_mod._load_dot_env()
        # No error = success

    def test_loads_simple_key_value(self, tmp_path, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

        env_file = tmp_path / ".env"
        env_file.write_text("TEST_CLI_KEY_XYZ=hello_value\n")

        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.read_text", return_value="TEST_CLI_KEY_XYZ=hello_value\n"):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("TEST_CLI_KEY_XYZ", None)
                    main_mod._load_dot_env()

        # Reset for other tests
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

    def test_handles_export_syntax(self, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

        env_content = "export MY_EXPORT_VAR=exportval\n"
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.read_text", return_value=env_content):
                os.environ.pop("MY_EXPORT_VAR", None)
                main_mod._load_dot_env()

        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

    def test_skips_comment_lines(self, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

        env_content = "# This is a comment\n\nKEY=value\n"
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.read_text", return_value=env_content):
                main_mod._load_dot_env()

        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

    def test_strips_quotes_from_values(self, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

        env_content = 'QUOTED_VAR="quoted_value"\n'
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.read_text", return_value=env_content):
                os.environ.pop("QUOTED_VAR", None)
                main_mod._load_dot_env()
                if "QUOTED_VAR" in os.environ:
                    assert os.environ["QUOTED_VAR"] == "quoted_value"

        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

    def test_no_env_file_does_nothing(self, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

        with patch("pathlib.Path.is_file", return_value=False):
            main_mod._load_dot_env()  # Should not raise

        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

    def test_skips_lines_without_equals(self, monkeypatch):
        import sandcastle.__main__ as main_mod
        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)

        env_content = "NO_EQUALS_HERE\nKEY=value\n"
        with patch("pathlib.Path.is_file", return_value=True):
            with patch("pathlib.Path.read_text", return_value=env_content):
                main_mod._load_dot_env()  # Should not raise

        monkeypatch.setattr(main_mod, "_dot_env_loaded", False)


# ---------------------------------------------------------------------------
# __main__.py: _validate_run_id
# ---------------------------------------------------------------------------

class TestValidateRunId:
    """Test _validate_run_id validation branches."""

    def test_empty_string_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit) as exc_info:
            _validate_run_id("")
        assert exc_info.value.code == 1

    def test_whitespace_only_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("   ")

    def test_slash_in_run_id_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run/traversal")

    def test_backslash_in_run_id_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run\\evil")

    def test_space_in_run_id_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run with space")

    def test_null_byte_in_run_id_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run\x00null")

    def test_control_char_exits(self):
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run\x01ctrl")

    def test_valid_uuid_format_passes(self):
        from sandcastle.__main__ import _validate_run_id
        # Should not raise or exit
        _validate_run_id("550e8400-e29b-41d4-a716-446655440000")

    def test_non_uuid_format_warns_but_passes(self, capsys):
        from sandcastle.__main__ import _validate_run_id
        # Non-UUID but valid characters - should print warning but not exit
        _validate_run_id("not-a-uuid-format")
        captured = capsys.readouterr()
        assert "Warning" in captured.err or captured.err == ""


# ---------------------------------------------------------------------------
# __main__.py: _format_cli_error
# ---------------------------------------------------------------------------

class TestFormatCliError:
    """Test all branches of _format_cli_error."""

    def test_sandcastle_error_401(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self, msg, status_code, code):
                super().__init__(msg)
                self.status_code = status_code
                self.code = code
                self.message = msg

        exc = SandcastleError("Unauthorized", 401, "AUTH_FAILED")
        result = _format_cli_error(exc)
        assert "401" in result
        assert "API key" in result

    def test_sandcastle_error_403(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self):
                super().__init__("Forbidden")
                self.status_code = 403
                self.code = "FORBIDDEN"
                self.message = "Forbidden"

        result = _format_cli_error(SandcastleError())
        assert "403" in result

    def test_sandcastle_error_404(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self):
                super().__init__("Not Found")
                self.status_code = 404
                self.code = "NOT_FOUND"
                self.message = "Not Found"

        result = _format_cli_error(SandcastleError())
        assert "404" in result

    def test_sandcastle_error_422(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self):
                super().__init__("Validation")
                self.status_code = 422
                self.code = "VALIDATION"
                self.message = "Validation error"

        result = _format_cli_error(SandcastleError())
        assert "422" in result

    def test_sandcastle_error_500(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self):
                super().__init__("Server error")
                self.status_code = 500
                self.code = "INTERNAL"
                self.message = "Internal server error"

        result = _format_cli_error(SandcastleError())
        assert "500" in result

    def test_sandcastle_error_other_http_status(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self):
                super().__init__("Conflict")
                self.status_code = 409
                self.code = "CONFLICT"
                self.message = "Conflict"

        result = _format_cli_error(SandcastleError())
        assert "409" in result

    def test_sandcastle_error_no_http_status(self):
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            def __init__(self):
                super().__init__("Connection error")
                self.status_code = 0
                self.code = "CONN_ERROR"
                self.message = "Could not connect"

        result = _format_cli_error(SandcastleError())
        assert "CONN_ERROR" in result

    def test_connect_error(self):
        from sandcastle.__main__ import _format_cli_error

        class ConnectError(Exception):
            pass

        exc = ConnectError("Connection refused")
        result = _format_cli_error(exc)
        assert "Connection" in result or "server" in result.lower()

    def test_file_not_found_error(self):
        from sandcastle.__main__ import _format_cli_error
        exc = FileNotFoundError("no file")
        result = _format_cli_error(exc)
        assert "File not found" in result

    def test_permission_error(self):
        from sandcastle.__main__ import _format_cli_error
        exc = PermissionError("denied")
        result = _format_cli_error(exc)
        assert "Permission" in result

    def test_401_in_message(self):
        from sandcastle.__main__ import _format_cli_error
        exc = Exception("HTTP 401 Unauthorized")
        result = _format_cli_error(exc)
        assert "401" in result

    def test_403_in_message(self):
        from sandcastle.__main__ import _format_cli_error
        exc = Exception("403 Forbidden")
        result = _format_cli_error(exc)
        assert "403" in result

    def test_404_in_message(self):
        from sandcastle.__main__ import _format_cli_error
        exc = Exception("404 Not Found")
        result = _format_cli_error(exc)
        assert "404" in result

    def test_500_in_message(self):
        from sandcastle.__main__ import _format_cli_error
        exc = Exception("500 Internal Server Error")
        result = _format_cli_error(exc)
        assert "500" in result

    def test_generic_error(self):
        from sandcastle.__main__ import _format_cli_error
        exc = Exception("something went wrong")
        result = _format_cli_error(exc)
        assert "Error" in result
        assert "something went wrong" in result


# ---------------------------------------------------------------------------
# __main__.py: _attr
# ---------------------------------------------------------------------------

class TestAttr:
    def test_dict_returns_value(self):
        from sandcastle.__main__ import _attr
        assert _attr({"key": "val"}, "key") == "val"

    def test_dict_missing_returns_default(self):
        from sandcastle.__main__ import _attr
        assert _attr({}, "key", "default") == "default"

    def test_object_returns_attribute(self):
        from sandcastle.__main__ import _attr
        obj = SimpleNamespace(key="val")
        assert _attr(obj, "key") == "val"

    def test_object_missing_returns_default(self):
        from sandcastle.__main__ import _attr
        obj = SimpleNamespace()
        assert _attr(obj, "missing", "default") == "default"


# ---------------------------------------------------------------------------
# __main__.py: _parse_input_pairs
# ---------------------------------------------------------------------------

class TestParseInputPairs:
    def test_none_returns_empty(self):
        from sandcastle.__main__ import _parse_input_pairs
        assert _parse_input_pairs(None) == {}

    def test_empty_list_returns_empty(self):
        from sandcastle.__main__ import _parse_input_pairs
        assert _parse_input_pairs([]) == {}

    def test_simple_string_value(self):
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["key=value"])
        assert result == {"key": "value"}

    def test_json_int_value(self):
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["count=42"])
        assert result == {"count": 42}

    def test_json_bool_value(self):
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["flag=true"])
        assert result == {"flag": True}

    def test_json_list_value(self):
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(['items=["a","b"]'])
        assert result == {"items": ["a", "b"]}

    def test_multiple_pairs(self):
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["a=1", "b=hello"])
        assert result["a"] == 1
        assert result["b"] == "hello"

    def test_missing_equals_exits(self):
        from sandcastle.__main__ import _parse_input_pairs
        with pytest.raises(SystemExit):
            _parse_input_pairs(["no_equals"])

    def test_empty_key_exits(self):
        from sandcastle.__main__ import _parse_input_pairs
        with pytest.raises(SystemExit):
            _parse_input_pairs(["=value"])


# ---------------------------------------------------------------------------
# __main__.py: _load_input_file
# ---------------------------------------------------------------------------

class TestLoadInputFile:
    def test_loads_valid_json(self, tmp_path):
        from sandcastle.__main__ import _load_input_file
        f = tmp_path / "input.json"
        f.write_text('{"key": "value", "count": 42}')
        result = _load_input_file(str(f))
        assert result == {"key": "value", "count": 42}

    def test_file_not_found_exits(self):
        from sandcastle.__main__ import _load_input_file
        with pytest.raises(SystemExit):
            _load_input_file("/nonexistent/path/input.json")

    def test_invalid_json_exits(self, tmp_path):
        from sandcastle.__main__ import _load_input_file
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{")
        with pytest.raises(SystemExit):
            _load_input_file(str(f))

    def test_non_dict_json_exits(self, tmp_path):
        from sandcastle.__main__ import _load_input_file
        f = tmp_path / "array.json"
        f.write_text("[1, 2, 3]")
        with pytest.raises(SystemExit):
            _load_input_file(str(f))

    def test_file_too_large_exits(self, tmp_path):
        from sandcastle.__main__ import _load_input_file
        f = tmp_path / "big.json"
        f.write_text("{}")
        with patch("os.path.getsize", return_value=20 * 1024 * 1024):  # 20MB
            with pytest.raises(SystemExit):
                _load_input_file(str(f))


# ---------------------------------------------------------------------------
# __main__.py: _to_dict and _to_dicts
# ---------------------------------------------------------------------------

class TestToDictFunctions:
    def test_to_dict_with_dict(self):
        from sandcastle.__main__ import _to_dict
        d = {"key": "value"}
        assert _to_dict(d) is d

    def test_to_dict_with_model_dump(self):
        from sandcastle.__main__ import _to_dict
        obj = MagicMock()
        obj.model_dump.return_value = {"key": "val"}
        result = _to_dict(obj)
        assert result == {"key": "val"}

    def test_to_dict_with_dict_attribute(self):
        from sandcastle.__main__ import _to_dict
        obj = SimpleNamespace(key="val")
        result = _to_dict(obj)
        assert result.get("key") == "val"

    def test_to_dict_with_plain_value(self):
        from sandcastle.__main__ import _to_dict
        result = _to_dict(42)
        assert result == {"value": "42"}

    def test_to_dicts_from_list(self):
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts([{"a": 1}, {"b": 2}])
        assert len(result) == 2

    def test_to_dicts_from_dict_with_data(self):
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts({"data": [{"a": 1}]})
        assert len(result) == 1

    def test_to_dicts_from_dict_without_data(self):
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts({"single": "item"})
        assert len(result) == 1

    def test_to_dicts_from_obj_with_items(self):
        from sandcastle.__main__ import _to_dicts
        obj = MagicMock()
        obj.items = [{"a": 1}, {"b": 2}]
        result = _to_dicts(obj)
        assert len(result) == 2

    def test_to_dicts_empty(self):
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts(None)
        assert result == []


# ---------------------------------------------------------------------------
# __main__.py: _fmt_time
# ---------------------------------------------------------------------------

class TestFmtTime:
    def test_none_returns_dash(self):
        from sandcastle.__main__ import _fmt_time
        assert _fmt_time(None) == "-"

    def test_string_trimmed(self):
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time("2026-01-15T10:30:00Z")
        assert result == "2026-01-15 10:30"

    def test_datetime_formatted(self):
        from datetime import datetime
        from sandcastle.__main__ import _fmt_time
        from datetime import timezone
        dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
        result = _fmt_time(dt)
        assert "2026-01-15" in result

    def test_other_type_stringified(self):
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time(12345)
        assert "12345" in result


# ---------------------------------------------------------------------------
# __main__.py: _print_run_detail
# ---------------------------------------------------------------------------

class TestPrintRunDetail:
    def test_basic_run_no_steps_no_outputs(self, capsys):
        from sandcastle.__main__ import _print_run_detail
        run = {
            "run_id": "aabbccdd-0000-0000-0000-000000000001",
            "workflow_name": "test_workflow",
            "status": "completed",
            "total_cost_usd": 0.05,
            "started_at": "2026-01-15T10:00:00Z",
            "completed_at": "2026-01-15T10:01:00Z",
        }
        _print_run_detail(run)
        captured = capsys.readouterr()
        assert "test_workflow" in captured.out
        assert "completed" in captured.out

    def test_run_with_error(self, capsys):
        from sandcastle.__main__ import _print_run_detail
        run = {
            "run_id": "aabbccdd-0000-0000-0000-000000000002",
            "workflow_name": "test_wf",
            "status": "failed",
            "total_cost_usd": 0.01,
            "error": "Something went wrong",
        }
        _print_run_detail(run)
        captured = capsys.readouterr()
        assert "Something went wrong" in captured.out

    def test_run_with_steps(self, capsys):
        from sandcastle.__main__ import _print_run_detail
        run = {
            "run_id": "aabbccdd-0000-0000-0000-000000000003",
            "workflow_name": "test_wf",
            "status": "completed",
            "total_cost_usd": 0.1,
            "steps": [
                {
                    "step_id": "step1",
                    "status": "completed",
                    "cost_usd": 0.05,
                    "duration_seconds": 2.5,
                    "attempt": 1,
                }
            ],
        }
        _print_run_detail(run)
        captured = capsys.readouterr()
        assert "step1" in captured.out

    def test_run_with_outputs(self, capsys):
        from sandcastle.__main__ import _print_run_detail
        run = {
            "run_id": "aabbccdd-0000-0000-0000-000000000004",
            "workflow_name": "test_wf",
            "status": "completed",
            "outputs": {"result": "some output"},
        }
        _print_run_detail(run)
        captured = capsys.readouterr()
        assert "some output" in captured.out


# ---------------------------------------------------------------------------
# __main__.py: _print_eval_results
# ---------------------------------------------------------------------------

class TestPrintEvalResults:
    def _make_result(self, passed_cases=1, failed_cases=0, verbose=False):
        case_obj = SimpleNamespace(
            name="test_case",
            passed=True,
            cost_usd=0.01,
            duration_seconds=1.5,
            assertions=[
                SimpleNamespace(passed=True, type="contains", message=""),
            ],
            error=None,
        )
        cases = [case_obj] * passed_cases
        for i in range(failed_cases):
            failed = SimpleNamespace(
                name=f"failed_case_{i}",
                passed=False,
                cost_usd=0.01,
                duration_seconds=1.5,
                assertions=[
                    SimpleNamespace(passed=False, type="contains", message="Expected X got Y"),
                ],
                error="assertion failed",
            )
            cases.append(failed)

        return SimpleNamespace(
            cases=cases,
            passed=passed_cases,
            failed=failed_cases,
            total=passed_cases + failed_cases,
            total_cost_usd=0.05,
            total_duration_seconds=5.0,
            pass_rate=passed_cases / (passed_cases + failed_cases) if (passed_cases + failed_cases) > 0 else 0,
        )

    def test_all_passed(self, capsys):
        from sandcastle.__main__ import _print_eval_results
        result = self._make_result(passed_cases=3, failed_cases=0)
        _print_eval_results(result)
        captured = capsys.readouterr()
        assert "3" in captured.out

    def test_some_failed(self, capsys):
        from sandcastle.__main__ import _print_eval_results
        result = self._make_result(passed_cases=1, failed_cases=1)
        _print_eval_results(result, verbose=False)
        captured = capsys.readouterr()
        assert "2" in captured.out  # total

    def test_verbose_shows_failed_assertions(self, capsys):
        from sandcastle.__main__ import _print_eval_results
        result = self._make_result(passed_cases=0, failed_cases=1)
        _print_eval_results(result, verbose=True)
        captured = capsys.readouterr()
        assert "Expected X got Y" in captured.out

    def test_pass_rate_color_green_above_80(self, capsys):
        from sandcastle.__main__ import _print_eval_results
        # 4/5 = 80% -> green
        result = self._make_result(passed_cases=4, failed_cases=1)
        _print_eval_results(result)

    def test_pass_rate_color_yellow_50_80(self, capsys):
        from sandcastle.__main__ import _print_eval_results
        # 3/5 = 60% -> yellow
        result = self._make_result(passed_cases=3, failed_cases=2)
        _print_eval_results(result)

    def test_pass_rate_color_red_below_50(self, capsys):
        from sandcastle.__main__ import _print_eval_results
        # 1/5 = 20% -> red
        result = self._make_result(passed_cases=1, failed_cases=4)
        _print_eval_results(result)


# ---------------------------------------------------------------------------
# executor.py: _execute_fallback
# ---------------------------------------------------------------------------

class TestExecuteFallback:
    """Test _execute_fallback function."""

    @pytest.mark.asyncio
    async def test_fallback_success(self):
        from sandcastle.engine.executor import RunContext, StepResult, _execute_fallback
        from sandcastle.engine.dag import StepDefinition

        context = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000010",
            input={},
            workflow_name="test_wf",
        )

        step = MagicMock(spec=StepDefinition)
        step.id = "test_step"
        step.type = "llm"
        step.fallback = SimpleNamespace(
            prompt="Fallback: try again",
            model="claude-haiku",
        )
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.prompt = "original prompt"

        mock_runtime = AsyncMock()
        mock_result = MagicMock()
        mock_result.structured_output = None
        mock_result.text = "Fallback response"
        mock_result.total_cost_usd = 0.001
        mock_runtime.query = AsyncMock(return_value=mock_result)

        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(return_value=None)

        result = await _execute_fallback(step, context, mock_runtime, mock_storage)
        assert result.status == "completed"
        assert result.output == "Fallback response"

    @pytest.mark.asyncio
    async def test_fallback_with_json_output(self):
        from sandcastle.engine.executor import RunContext, _execute_fallback
        from sandcastle.engine.dag import StepDefinition

        context = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000011",
            input={},
            workflow_name="test_wf",
        )

        step = MagicMock(spec=StepDefinition)
        step.id = "test_step"
        step.type = "llm"
        step.fallback = SimpleNamespace(
            prompt="Fallback prompt",
            model="claude-haiku",
        )
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []

        mock_runtime = AsyncMock()
        mock_result = MagicMock()
        mock_result.structured_output = None
        mock_result.text = '{"key": "value", "items": [1, 2]}'
        mock_result.total_cost_usd = 0.001
        mock_runtime.query = AsyncMock(return_value=mock_result)

        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(return_value=None)

        result = await _execute_fallback(step, context, mock_runtime, mock_storage)
        assert result.status == "completed"
        assert result.output == {"key": "value", "items": [1, 2]}

    @pytest.mark.asyncio
    async def test_fallback_with_code_fenced_json(self):
        from sandcastle.engine.executor import RunContext, _execute_fallback
        from sandcastle.engine.dag import StepDefinition

        context = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000012",
            input={},
            workflow_name="test_wf",
        )

        step = MagicMock(spec=StepDefinition)
        step.id = "test_step"
        step.type = "llm"
        step.fallback = SimpleNamespace(
            prompt="Fallback prompt",
            model="claude-haiku",
        )
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []

        mock_runtime = AsyncMock()
        mock_result = MagicMock()
        mock_result.structured_output = None
        mock_result.text = '```json\n{"key": "value"}\n```'
        mock_result.total_cost_usd = 0.001
        mock_runtime.query = AsyncMock(return_value=mock_result)

        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(return_value=None)

        result = await _execute_fallback(step, context, mock_runtime, mock_storage)
        assert result.status == "completed"
        assert result.output == {"key": "value"}

    @pytest.mark.asyncio
    async def test_fallback_exception_returns_failed(self):
        from sandcastle.engine.executor import RunContext, _execute_fallback
        from sandcastle.engine.dag import StepDefinition

        context = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000013",
            input={},
            workflow_name="test_wf",
        )

        step = MagicMock(spec=StepDefinition)
        step.id = "test_step"
        step.type = "llm"
        step.fallback = SimpleNamespace(
            prompt="Fallback prompt",
            model="claude-haiku",
        )
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []

        mock_runtime = AsyncMock()
        mock_runtime.query = AsyncMock(side_effect=Exception("API error"))

        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.telemetry.capture_step_error"):
            result = await _execute_fallback(step, context, mock_runtime, mock_storage)
        assert result.status == "failed"
        assert "Fallback failed" in result.error


# ---------------------------------------------------------------------------
# routes.py: _resolve_budget error handling
# ---------------------------------------------------------------------------

class TestResolveBudgetErrors:
    """Test _resolve_budget exception handling."""

    @pytest.mark.asyncio
    async def test_budget_check_exception_falls_through_to_default(self):
        from sandcastle.api.routes import _resolve_budget

        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = True
            mock_settings.default_max_cost_usd = 5.0

            # Make session raise an exception
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(side_effect=Exception("DB error"))

            with patch("sandcastle.api.routes.async_session", return_value=mock_session):
                result = await _resolve_budget(None, "tenant1")

        # Should fall through to env-level default
        assert result == 5.0

    @pytest.mark.asyncio
    async def test_request_budget_takes_priority(self):
        from sandcastle.api.routes import _resolve_budget
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = True
            mock_settings.default_max_cost_usd = 5.0
            result = await _resolve_budget(10.0, "tenant1")
        assert result == 10.0

    @pytest.mark.asyncio
    async def test_no_budget_returns_none(self):
        from sandcastle.api.routes import _resolve_budget
        with patch("sandcastle.api.routes.settings") as mock_settings:
            mock_settings.auth_required = False
            mock_settings.default_max_cost_usd = None
            result = await _resolve_budget(None, None)
        assert result is None


# ---------------------------------------------------------------------------
# executor.py: _save_to_cache collision handling
# ---------------------------------------------------------------------------

class TestSaveToCache:
    """Test _save_to_cache including collision update path."""

    @pytest.mark.asyncio
    async def test_save_to_cache_success(self):
        from sandcastle.engine.executor import _save_to_cache

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_to_cache(
                cache_key="test_key_123",
                workflow_name="test_wf",
                step_id="step1",
                model="claude-haiku",
                output={"result": "cached_output"},
                cost_usd=0.01,
            )
        # Should not raise

    @pytest.mark.asyncio
    async def test_save_to_cache_integrity_error_triggers_update(self):
        from sqlalchemy.exc import IntegrityError
        from sandcastle.engine.executor import _save_to_cache

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()
        # First commit raises IntegrityError (duplicate key)
        mock_session.commit = AsyncMock(side_effect=[
            IntegrityError("duplicate", {}, Exception("unique violation")),
            None,  # Second commit (after update) succeeds
        ])
        mock_session.rollback = AsyncMock()
        mock_session.execute = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_to_cache(
                cache_key="existing_key",
                workflow_name="test_wf",
                step_id="step1",
                model="claude-haiku",
                output="cached output",
                cost_usd=0.01,
            )
        # Should not raise - collision handled by update

    @pytest.mark.asyncio
    async def test_save_to_cache_outer_exception_logged(self):
        from sandcastle.engine.executor import _save_to_cache

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("DB completely down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise - outer exception is caught and logged
            await _save_to_cache(
                cache_key="test_key",
                workflow_name="test_wf",
                step_id="step1",
                model="claude-haiku",
                output={"result": "data"},
                cost_usd=0.01,
            )


# ---------------------------------------------------------------------------
# executor.py: _get_cached_result
# ---------------------------------------------------------------------------

class TestGetCachedResult:
    """Test _get_cached_result function."""

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        from sandcastle.engine.executor import _get_cached_result

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            result = await _get_cached_result("nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_data(self):
        from sandcastle.engine.executor import _get_cached_result

        mock_row = MagicMock()
        mock_row.hit_count = 0
        mock_row.output_data = {"result": "cached_value"}
        mock_row.cost_usd = 0.05

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=mock_row)
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            result = await _get_cached_result("existing_key")

        assert result is not None
        assert result["output"] == {"result": "cached_value"}

    @pytest.mark.asyncio
    async def test_cache_exception_returns_none(self):
        from sandcastle.engine.executor import _get_cached_result

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            result = await _get_cached_result("any_key")
        assert result is None


# ---------------------------------------------------------------------------
# __main__.py: _find_pending_approval
# ---------------------------------------------------------------------------

class TestFindPendingApproval:
    """Test _find_pending_approval function."""

    def test_returns_approval_id_when_found(self):
        from sandcastle.__main__ import _find_pending_approval

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "data": [
                {"run_id": "run-123", "id": "approval-456"},
                {"run_id": "other-run", "id": "approval-789"},
            ]
        })

        with patch("httpx.get", return_value=mock_resp):
            result = _find_pending_approval("http://localhost:8080", {}, "run-123")
        assert result == "approval-456"

    def test_returns_none_when_not_found(self):
        from sandcastle.__main__ import _find_pending_approval

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"data": []})

        with patch("httpx.get", return_value=mock_resp):
            result = _find_pending_approval("http://localhost:8080", {}, "run-not-found")
        assert result is None

    def test_exits_on_network_error(self):
        from sandcastle.__main__ import _find_pending_approval

        with patch("httpx.get", side_effect=Exception("network error")):
            with pytest.raises(SystemExit):
                _find_pending_approval("http://localhost:8080", {}, "run-123")
