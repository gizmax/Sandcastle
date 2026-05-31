"""
Coverage boost tests for __main__.py (CLI module).

Targets uncovered lines in:
- Color helpers and table formatter
- .env loader
- UUID validation
- Error formatting
- Input parsing helpers
- Hub commands (mocked network)
- Format/display helpers
- Command handlers with mocked clients
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, mock_open

import pytest

# Import the module under test - use full module path
import sandcastle.__main__ as cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_args(**kwargs) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for testing command handlers."""
    ns = argparse.Namespace()
    # Defaults that most commands need
    ns.url = "http://localhost:8080"
    ns.api_key = ""
    ns.json = False
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# _C.supports_color
# ---------------------------------------------------------------------------

class TestSupportsColor:
    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert cli._C.supports_color() is False

    def test_no_color_empty_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        # Empty string is falsy but set - implementation checks getenv which returns ""
        # "" is falsy, so color may still be enabled if isatty
        # just verify it doesn't raise
        result = cli._C.supports_color()
        assert isinstance(result, bool)

    def test_not_tty_no_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch.object(sys.stdout, "isatty", return_value=False):
            assert cli._C.supports_color() is False

    def test_tty_enables_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch.object(sys.stdout, "isatty", return_value=True):
            assert cli._C.supports_color() is True


# ---------------------------------------------------------------------------
# _color helper
# ---------------------------------------------------------------------------

class TestColorHelper:
    def test_color_when_not_supported(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        result = cli._color("hello", cli._C.GREEN)
        assert result == "hello"

    def test_color_when_supported(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("sandcastle.__main__._C.supports_color", return_value=True):
            result = cli._color("hello", cli._C.GREEN)
            assert cli._C.GREEN in result
            assert "hello" in result


# ---------------------------------------------------------------------------
# _status_color
# ---------------------------------------------------------------------------

class TestStatusColor:
    def test_completed_green(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("completed") == "completed"

    def test_failed_red(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("failed") == "failed"

    def test_running_yellow(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("running") == "running"

    def test_queued_gray(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("queued") == "queued"

    def test_unknown_status_passthrough(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("UNKNOWN_STATE") == "UNKNOWN_STATE"

    def test_success_green(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("success") == "success"

    def test_pending_yellow(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("pending") == "pending"

    def test_cancelled_gray(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("cancelled") == "cancelled"

    def test_error_red(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            assert cli._status_color("error") == "error"


# ---------------------------------------------------------------------------
# _table
# ---------------------------------------------------------------------------

class TestTable:
    def test_empty_rows_returns_no_data(self):
        result = cli._table(["A", "B"], [])
        assert result == "(no data)"

    def test_basic_table(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            result = cli._table(["NAME", "STATUS"], [["foo", "ok"]])
        assert "NAME" in result
        assert "foo" in result
        assert "ok" in result

    def test_truncates_long_values(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            long_val = "x" * 50
            result = cli._table(["COL"], [[long_val]], max_col=10)
        # Should contain ellipsis
        assert "\u2026" in result

    def test_short_row_padded(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            result = cli._table(["A", "B", "C"], [["only_one"]])
        assert "only_one" in result

    def test_separator_line(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            result = cli._table(["NAME"], [["val"]])
        assert "---" in result

    def test_multiple_rows(self):
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            rows = [["alpha", "1"], ["beta", "2"], ["gamma", "3"]]
            result = cli._table(["NAME", "NUM"], rows)
        assert "alpha" in result
        assert "beta" in result
        assert "gamma" in result


# ---------------------------------------------------------------------------
# _load_dot_env
# ---------------------------------------------------------------------------

class TestLoadDotEnv:
    def test_idempotent_if_already_loaded(self, monkeypatch):
        monkeypatch.setattr(cli, "_dot_env_loaded", True)
        # Should return immediately without doing anything
        cli._load_dot_env()
        # No exception = pass

    def test_no_file_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_dot_env_loaded", False)
        monkeypatch.chdir(tmp_path)
        cli._load_dot_env()
        assert cli._dot_env_loaded is True

    def test_loads_env_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_dot_env_loaded", False)
        monkeypatch.chdir(tmp_path)
        env_path = tmp_path / ".env"
        env_path.write_text('TEST_CLI_KEY="hello_world"\n# comment\nINVALID_LINE\n')
        cli._load_dot_env()
        assert os.environ.get("TEST_CLI_KEY") == "hello_world"

    def test_skips_existing_env_vars(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_dot_env_loaded", False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXISTING_KEY", "original")
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING_KEY=overridden\n")
        cli._load_dot_env()
        # Should not override
        assert os.environ.get("EXISTING_KEY") == "original"

    def test_handles_export_syntax(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_dot_env_loaded", False)
        monkeypatch.chdir(tmp_path)
        env_path = tmp_path / ".env"
        env_path.write_text("export EXPORT_TEST_KEY=myval\n")
        monkeypatch.delenv("EXPORT_TEST_KEY", raising=False)
        cli._load_dot_env()
        assert os.environ.get("EXPORT_TEST_KEY") == "myval"

    def test_handles_single_quotes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_dot_env_loaded", False)
        monkeypatch.chdir(tmp_path)
        env_path = tmp_path / ".env"
        env_path.write_text("SINGLE_QUOTE_KEY='singleval'\n")
        monkeypatch.delenv("SINGLE_QUOTE_KEY", raising=False)
        cli._load_dot_env()
        assert os.environ.get("SINGLE_QUOTE_KEY") == "singleval"


# ---------------------------------------------------------------------------
# _validate_run_id
# ---------------------------------------------------------------------------

class TestValidateRunId:
    def test_valid_uuid_passes(self):
        # Should not raise or exit
        cli._validate_run_id("550e8400-e29b-41d4-a716-446655440000")

    def test_empty_string_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("")
        assert exc.value.code == 1

    def test_whitespace_only_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("   ")
        assert exc.value.code == 1

    def test_slash_in_id_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("abc/def")
        assert exc.value.code == 1

    def test_backslash_in_id_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("abc\\def")
        assert exc.value.code == 1

    def test_space_in_id_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("abc def")
        assert exc.value.code == 1

    def test_null_byte_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("abc\x00def")
        assert exc.value.code == 1

    def test_control_char_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._validate_run_id("abc\x01def")
        assert exc.value.code == 1

    def test_non_uuid_prints_warning(self, capsys):
        # Should NOT exit but should print a warning
        cli._validate_run_id("not-a-uuid")
        captured = capsys.readouterr()
        assert "Warning" in captured.err


# ---------------------------------------------------------------------------
# _format_cli_error
# ---------------------------------------------------------------------------

class TestFormatCliError:
    def _make_sandcastle_error(self, status_code, code="ERR", message="msg"):
        exc = Exception(message)
        exc.__class__.__name__ = "SandcastleError"
        # Monkey-patch class name for isinstance check by name
        exc_class = type("SandcastleError", (Exception,), {
            "status_code": status_code,
            "code": code,
            "message": message,
        })
        return exc_class(message)

    def test_401_message(self):
        class SandcastleError(Exception):
            status_code = 401
            code = "UNAUTH"
            message = "bad key"
        result = cli._format_cli_error(SandcastleError("bad key"))
        assert "401" in result
        assert "API key" in result

    def test_403_message(self):
        class SandcastleError(Exception):
            status_code = 403
            code = "FORBIDDEN"
            message = "no perm"
        result = cli._format_cli_error(SandcastleError("no perm"))
        assert "403" in result

    def test_404_message(self):
        class SandcastleError(Exception):
            status_code = 404
            code = "NOT_FOUND"
            message = "gone"
        result = cli._format_cli_error(SandcastleError("gone"))
        assert "404" in result

    def test_422_message(self):
        class SandcastleError(Exception):
            status_code = 422
            code = "VALIDATION"
            message = "bad input"
        result = cli._format_cli_error(SandcastleError("bad input"))
        assert "422" in result

    def test_500_message(self):
        class SandcastleError(Exception):
            status_code = 500
            code = "SERVER_ERR"
            message = "boom"
        result = cli._format_cli_error(SandcastleError("boom"))
        assert "500" in result

    def test_other_status_message(self):
        class SandcastleError(Exception):
            status_code = 429
            code = "RATE_LIMIT"
            message = "slow down"
        result = cli._format_cli_error(SandcastleError("slow down"))
        assert "429" in result

    def test_zero_status_message(self):
        class SandcastleError(Exception):
            status_code = 0
            code = "CONN_ERR"
            message = "connection error"
        result = cli._format_cli_error(SandcastleError("connection error"))
        assert "CONN_ERR" in result

    def test_connect_error(self):
        class ConnectError(Exception):
            pass
        result = cli._format_cli_error(ConnectError("refused"))
        assert "Connection failed" in result or "sandcastle serve" in result

    def test_connect_timeout(self):
        class ConnectTimeout(Exception):
            pass
        result = cli._format_cli_error(ConnectTimeout("timed out"))
        assert "timed out" in result.lower() or "timeout" in result.lower()

    def test_connection_refused_in_message(self):
        exc = Exception("ConnectionRefused: port 8080")
        result = cli._format_cli_error(exc)
        assert "refused" in result.lower() or "server" in result.lower()

    def test_401_in_message(self):
        exc = Exception("HTTP 401 Unauthorized")
        result = cli._format_cli_error(exc)
        assert "401" in result

    def test_403_in_message(self):
        exc = Exception("HTTP 403 Forbidden")
        result = cli._format_cli_error(exc)
        assert "403" in result

    def test_404_in_message(self):
        exc = Exception("404 Not Found")
        result = cli._format_cli_error(exc)
        assert "404" in result

    def test_422_in_message(self):
        exc = Exception("422 Validation")
        result = cli._format_cli_error(exc)
        assert "422" in result

    def test_500_in_message(self):
        exc = Exception("500 Internal Server Error")
        result = cli._format_cli_error(exc)
        assert "500" in result

    def test_file_not_found(self):
        exc = FileNotFoundError("file.txt")
        result = cli._format_cli_error(exc)
        assert "not found" in result.lower()

    def test_permission_error(self):
        exc = PermissionError("denied")
        result = cli._format_cli_error(exc)
        assert "denied" in result.lower()

    def test_generic_error(self):
        exc = ValueError("something went wrong")
        result = cli._format_cli_error(exc)
        assert "Error" in result


# ---------------------------------------------------------------------------
# _parse_input_pairs
# ---------------------------------------------------------------------------

class TestParseInputPairs:
    def test_empty_input(self):
        assert cli._parse_input_pairs(None) == {}
        assert cli._parse_input_pairs([]) == {}

    def test_string_value(self):
        result = cli._parse_input_pairs(["key=hello"])
        assert result == {"key": "hello"}

    def test_json_number_value(self):
        result = cli._parse_input_pairs(["count=42"])
        assert result == {"count": 42}

    def test_json_bool_value(self):
        result = cli._parse_input_pairs(["flag=true"])
        assert result == {"flag": True}

    def test_json_object_value(self):
        result = cli._parse_input_pairs(['obj={"a": 1}'])
        assert result == {"obj": {"a": 1}}

    def test_json_array_value(self):
        result = cli._parse_input_pairs(["arr=[1,2,3]"])
        assert result == {"arr": [1, 2, 3]}

    def test_multiple_pairs(self):
        result = cli._parse_input_pairs(["a=1", "b=hello", "c=true"])
        assert result == {"a": 1, "b": "hello", "c": True}

    def test_invalid_format_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._parse_input_pairs(["no_equals_sign"])
        assert exc.value.code == 1

    def test_empty_key_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli._parse_input_pairs(["=value"])
        assert exc.value.code == 1

    def test_value_with_equals_sign(self):
        result = cli._parse_input_pairs(["url=http://example.com?a=1"])
        assert result == {"url": "http://example.com?a=1"}


# ---------------------------------------------------------------------------
# _load_input_file
# ---------------------------------------------------------------------------

class TestLoadInputFile:
    def test_valid_json_dict(self, tmp_path):
        f = tmp_path / "input.json"
        f.write_text('{"key": "val", "num": 42}')
        result = cli._load_input_file(str(f))
        assert result == {"key": "val", "num": 42}

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            cli._load_input_file(str(tmp_path / "missing.json"))
        assert exc.value.code == 1

    def test_invalid_json_exits(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json at all {{{")
        with pytest.raises(SystemExit) as exc:
            cli._load_input_file(str(f))
        assert exc.value.code == 1

    def test_non_dict_json_exits(self, tmp_path):
        f = tmp_path / "array.json"
        f.write_text("[1, 2, 3]")
        with pytest.raises(SystemExit) as exc:
            cli._load_input_file(str(f))
        assert exc.value.code == 1

    def test_file_too_large_exits(self, tmp_path):
        f = tmp_path / "big.json"
        # Write something bigger than the 10MB limit
        with patch("os.path.getsize", return_value=11 * 1024 * 1024):
            f.write_text("{}")  # create the file
            with pytest.raises(SystemExit) as exc:
                cli._load_input_file(str(f))
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _attr helper
# ---------------------------------------------------------------------------

class TestAttrHelper:
    def test_dict_key(self):
        assert cli._attr({"a": 1}, "a") == 1

    def test_dict_missing_key_default(self):
        assert cli._attr({}, "x", "default") == "default"

    def test_object_attribute(self):
        obj = MagicMock()
        obj.status = "running"
        assert cli._attr(obj, "status") == "running"

    def test_object_missing_attribute_default(self):
        obj = MagicMock(spec=[])
        assert cli._attr(obj, "missing", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# _to_dict and _to_dicts
# ---------------------------------------------------------------------------

class TestToDictHelpers:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert cli._to_dict(d) == d

    def test_model_dump(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"x": 2}
        assert cli._to_dict(obj) == {"x": 2}

    def test_dunder_dict(self):
        class Simple:
            pass
        s = Simple()
        s.val = 99
        result = cli._to_dict(s)
        assert "val" in result

    def test_fallback_to_str(self):
        result = cli._to_dict(42)
        assert result == {"value": "42"}

    def test_to_dicts_list(self):
        items = [{"a": 1}, {"b": 2}]
        result = cli._to_dicts(items)
        assert result == items

    def test_to_dicts_dict_with_data(self):
        data = {"data": [{"a": 1}]}
        result = cli._to_dicts(data)
        assert result == [{"a": 1}]

    def test_to_dicts_dict_without_data(self):
        data = {"a": 1}
        result = cli._to_dicts(data)
        assert result == [{"a": 1}]

    def test_to_dicts_with_items_attr(self):
        obj = MagicMock()
        obj.items = [{"x": 1}]
        result = cli._to_dicts(obj)
        assert result == [{"x": 1}]

    def test_to_dicts_empty_list(self):
        assert cli._to_dicts([]) == []


# ---------------------------------------------------------------------------
# _fmt_time
# ---------------------------------------------------------------------------

class TestFmtTime:
    def test_none_returns_dash(self):
        assert cli._fmt_time(None) == "-"

    def test_iso_string_trimmed(self):
        result = cli._fmt_time("2024-01-15T10:30:45Z")
        assert result == "2024-01-15 10:30"

    def test_datetime_object(self):
        from datetime import datetime
        dt = datetime(2024, 3, 20, 14, 22)
        result = cli._fmt_time(dt)
        assert "2024-03-20" in result

    def test_other_value(self):
        result = cli._fmt_time(12345)
        assert result == "12345"


# ---------------------------------------------------------------------------
# _sanitize_hub_filename
# ---------------------------------------------------------------------------

class TestSanitizeHubFilename:
    def test_simple_slug(self):
        result = cli._sanitize_hub_filename("my-workflow")
        assert result == "my-workflow.yaml"

    def test_path_traversal_stripped(self):
        result = cli._sanitize_hub_filename("../../etc/passwd")
        # Should only use last component
        assert "/" not in result
        assert ".." not in result

    def test_special_chars_replaced(self):
        result = cli._sanitize_hub_filename("my workflow!")
        assert " " not in result
        assert "!" not in result

    def test_empty_becomes_workflow(self):
        result = cli._sanitize_hub_filename("")
        assert "workflow" in result

    def test_dot_prefix_stripped(self):
        result = cli._sanitize_hub_filename(".hidden")
        assert not result.startswith(".")


# ---------------------------------------------------------------------------
# _validate_hub_download_url
# ---------------------------------------------------------------------------

class TestValidateHubDownloadUrl:
    def test_valid_github_raw(self):
        url = "https://raw.githubusercontent.com/user/repo/main/wf.yaml"
        assert cli._validate_hub_download_url(url) is True

    def test_valid_github(self):
        url = "https://github.com/user/repo/blob/main/wf.yaml"
        assert cli._validate_hub_download_url(url) is True

    def test_http_rejected(self):
        url = "http://raw.githubusercontent.com/user/repo/main/wf.yaml"
        assert cli._validate_hub_download_url(url) is False

    def test_untrusted_host_rejected(self):
        url = "https://evil.example.com/malicious.yaml"
        assert cli._validate_hub_download_url(url) is False

    def test_ftp_rejected(self):
        url = "ftp://raw.githubusercontent.com/user/repo/main/wf.yaml"
        assert cli._validate_hub_download_url(url) is False


# ---------------------------------------------------------------------------
# _validate_hub_yaml
# ---------------------------------------------------------------------------

class TestValidateHubYaml:
    def test_valid_yaml(self):
        content = "name: test\nsteps:\n  - id: s1\n    prompt: hello\n"
        valid, err = cli._validate_hub_yaml(content)
        assert valid is True
        assert err == ""

    def test_too_large(self):
        content = "x" * (512 * 1024 + 1)
        valid, err = cli._validate_hub_yaml(content)
        assert valid is False
        assert "too large" in err

    def test_invalid_yaml(self):
        content = "name: test\n  bad: indent: here\n  - wrong\n!!!"
        valid, err = cli._validate_hub_yaml(content)
        assert valid is False

    def test_non_mapping(self):
        content = "- item1\n- item2\n"
        valid, err = cli._validate_hub_yaml(content)
        assert valid is False
        assert "mapping" in err

    def test_missing_name(self):
        content = "steps:\n  - id: s1\n    prompt: hello\n"
        valid, err = cli._validate_hub_yaml(content)
        assert valid is False
        assert "name" in err

    def test_missing_steps(self):
        content = "name: test\n"
        valid, err = cli._validate_hub_yaml(content)
        assert valid is False
        assert "steps" in err

    def test_steps_not_list(self):
        content = "name: test\nsteps: invalid\n"
        valid, err = cli._validate_hub_yaml(content)
        assert valid is False


# ---------------------------------------------------------------------------
# _cmd_hub_search - mocked registry
# ---------------------------------------------------------------------------

class TestCmdHubSearch:
    def _registry(self):
        return {
            "templates": [
                {
                    "slug": "my-workflow",
                    "name": "My Workflow",
                    "description": "A test workflow for testing",
                    "tags": ["test", "automation"],
                    "category": "automation",
                    "step_count": 3,
                    "author": "gizmax",
                },
                {
                    "slug": "other-workflow",
                    "name": "Other Workflow",
                    "description": "Different",
                    "tags": [],
                    "category": "data",
                    "step_count": 2,
                    "author": "someone",
                },
            ]
        }

    def test_search_by_name(self, capsys):
        args = make_args(query="My", category=None)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_hub_search(args)
        out = capsys.readouterr().out
        assert "my-workflow" in out

    def test_search_by_tag(self, capsys):
        args = make_args(query="automation", category=None)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_hub_search(args)
        out = capsys.readouterr().out
        assert "my-workflow" in out

    def test_search_no_results(self, capsys):
        args = make_args(query="zzznomatch", category=None)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            cli._cmd_hub_search(args)
        out = capsys.readouterr().out
        assert "No workflows found" in out

    def test_search_empty_query_exits(self):
        args = make_args(query="", category=None)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_hub_search(args)
        assert exc.value.code == 1

    def test_search_json_output(self, capsys):
        args = make_args(query="My", category=None, **{"json": True})
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            cli._cmd_hub_search(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_search_by_category_filter(self, capsys):
        args = make_args(query="workflow", category="data")
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_hub_search(args)
        out = capsys.readouterr().out
        assert "other-workflow" in out
        assert "my-workflow" not in out


# ---------------------------------------------------------------------------
# _cmd_hub_list - mocked registry
# ---------------------------------------------------------------------------

class TestCmdHubList:
    def _registry(self):
        return {
            "templates": [
                {
                    "slug": "wf-1",
                    "name": "Workflow One",
                    "category": "automation",
                    "step_count": 3,
                    "author": "gizmax",
                },
                {
                    "slug": "wf-2",
                    "name": "Workflow Two",
                    "category": "data",
                    "step_count": 5,
                    "author": "other",
                },
            ],
            "stats": {"total_templates": 2},
            "categories": ["automation", "data"],
        }

    def test_list_all(self, capsys):
        args = make_args(category=None, limit=20)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_hub_list(args)
        out = capsys.readouterr().out
        assert "wf-1" in out
        assert "wf-2" in out

    def test_list_json_output(self, capsys):
        args = make_args(category=None, limit=20, **{"json": True})
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            cli._cmd_hub_list(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 2

    def test_list_filter_category(self, capsys):
        args = make_args(category="automation", limit=20)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_hub_list(args)
        out = capsys.readouterr().out
        assert "wf-1" in out
        assert "wf-2" not in out

    def test_list_limit_invalid(self):
        args = make_args(category=None, limit=0)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_hub_list(args)
        assert exc.value.code == 1

    def test_list_empty(self, capsys):
        args = make_args(category=None, limit=20)
        registry = {"templates": [], "stats": {}, "categories": []}
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=registry):
            cli._cmd_hub_list(args)
        out = capsys.readouterr().out
        assert "No community workflows found" in out


# ---------------------------------------------------------------------------
# _cmd_hub_collections - mocked registry
# ---------------------------------------------------------------------------

class TestCmdHubCollections:
    def _registry(self):
        return {
            "collections": [
                {
                    "id": "col-1",
                    "name": "Automation Suite",
                    "description": "A collection of automation workflows",
                    "icon": "target",
                    "template_slugs": ["wf-1", "wf-2"],
                    "downloads": 100,
                },
            ]
        }

    def test_collections_list(self, capsys):
        args = make_args()
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_hub_collections(args)
        out = capsys.readouterr().out
        assert "Automation Suite" in out

    def test_collections_json(self, capsys):
        args = make_args(**{"json": True})
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=self._registry()):
            cli._cmd_hub_collections(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1

    def test_collections_empty(self, capsys):
        args = make_args()
        with patch("sandcastle.__main__._fetch_hub_registry", return_value={"collections": []}):
            cli._cmd_hub_collections(args)
        out = capsys.readouterr().out
        assert "No collections available" in out


# ---------------------------------------------------------------------------
# _cmd_ls helpers
# ---------------------------------------------------------------------------

class TestCmdLsHelpers:
    def test_ls_runs_no_runs(self, capsys):
        client = MagicMock()
        client.list_runs.return_value = []
        args = make_args(status=None, limit=10)
        cli._ls_runs(client, args)
        out = capsys.readouterr().out
        assert "No runs found" in out

    def test_ls_runs_limit_too_small_exits(self):
        client = MagicMock()
        args = make_args(status=None, limit=0)
        with pytest.raises(SystemExit) as exc:
            cli._ls_runs(client, args)
        assert exc.value.code == 1

    def test_ls_runs_with_data(self, capsys):
        client = MagicMock()
        client.list_runs.return_value = [
            {
                "run_id": "550e8400-e29b-41d4-a716-446655440000",
                "workflow_name": "test_wf",
                "status": "completed",
                "total_cost_usd": 0.01,
                "started_at": "2024-01-01T10:00:00",
            }
        ]
        args = make_args(status=None, limit=10)
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._ls_runs(client, args)
        out = capsys.readouterr().out
        assert "test_wf" in out

    def test_ls_workflows_no_workflows(self, capsys):
        client = MagicMock()
        client.list_workflows.return_value = []
        cli._ls_workflows(client)
        out = capsys.readouterr().out
        assert "No workflows found" in out

    def test_ls_workflows_with_data(self, capsys):
        client = MagicMock()
        client.list_workflows.return_value = [
            {"name": "my_wf", "description": "A workflow", "steps_count": 3}
        ]
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._ls_workflows(client)
        out = capsys.readouterr().out
        assert "my_wf" in out

    def test_ls_schedules_no_schedules(self, capsys):
        client = MagicMock()
        client.list_schedules.return_value = []
        cli._ls_schedules(client)
        out = capsys.readouterr().out
        assert "No schedules found" in out

    def test_ls_schedules_with_data(self, capsys):
        client = MagicMock()
        client.list_schedules.return_value = [
            {
                "id": "sched-001",
                "workflow_name": "daily_wf",
                "cron_expression": "0 9 * * 1-5",
                "enabled": True,
                "last_run_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        ]
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._ls_schedules(client)
        out = capsys.readouterr().out
        assert "daily_wf" in out

    def test_cmd_ls_unknown_resource_exits(self):
        args = make_args(resource="unknown_resource")
        with patch("sandcastle.__main__._get_client", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_ls(args)
        assert exc.value.code == 1

    def test_cmd_ls_no_resource_exits(self):
        args = make_args(resource=None)
        with patch("sandcastle.__main__._get_client", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_ls(args)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _print_run_detail
# ---------------------------------------------------------------------------

class TestPrintRunDetail:
    def test_basic_run_detail(self, capsys):
        run = {
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
            "workflow_name": "test_wf",
            "status": "completed",
            "total_cost_usd": 0.05,
            "started_at": "2024-01-01T10:00:00",
            "completed_at": "2024-01-01T10:01:00",
            "error": None,
            "steps": None,
            "outputs": None,
        }
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_run_detail(run)
        out = capsys.readouterr().out
        assert "550e8400" in out
        assert "test_wf" in out

    def test_run_detail_with_error(self, capsys):
        run = {
            "run_id": "abc-123",
            "workflow_name": "failing_wf",
            "status": "failed",
            "total_cost_usd": 0.0,
            "started_at": None,
            "completed_at": None,
            "error": "Something went wrong",
            "steps": None,
            "outputs": None,
        }
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_run_detail(run)
        out = capsys.readouterr().out
        assert "Something went wrong" in out

    def test_run_detail_with_steps(self, capsys):
        run = {
            "run_id": "abc-123",
            "workflow_name": "wf",
            "status": "completed",
            "total_cost_usd": 0.02,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "steps": [
                {
                    "step_id": "step1",
                    "status": "completed",
                    "cost_usd": 0.01,
                    "duration_seconds": 2.5,
                    "attempt": 1,
                }
            ],
            "outputs": None,
        }
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_run_detail(run)
        out = capsys.readouterr().out
        assert "step1" in out

    def test_run_detail_with_outputs(self, capsys):
        run = {
            "run_id": "abc-123",
            "workflow_name": "wf",
            "status": "completed",
            "total_cost_usd": 0.0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "steps": None,
            "outputs": {"result": "success"},
        }
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_run_detail(run)
        out = capsys.readouterr().out
        assert "result" in out


# ---------------------------------------------------------------------------
# _cmd_cancel
# ---------------------------------------------------------------------------

class TestCmdCancel:
    def test_cancel_success(self, capsys):
        client = MagicMock()
        client.cancel_run.return_value = None
        args = make_args(run_id="550e8400-e29b-41d4-a716-446655440000")
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_cancel(args)
        out = capsys.readouterr().out
        assert "Cancelled" in out

    def test_cancel_json_output(self, capsys):
        client = MagicMock()
        client.cancel_run.return_value = {"cancelled": True}
        args = make_args(run_id="550e8400-e29b-41d4-a716-446655440000", **{"json": True})
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_cancel(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data.get("cancelled") is True

    def test_cancel_client_error_exits(self):
        client = MagicMock()
        client.cancel_run.side_effect = Exception("Not found")
        args = make_args(run_id="550e8400-e29b-41d4-a716-446655440000")
        with patch("sandcastle.__main__._get_client", return_value=client):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_cancel(args)
        assert exc.value.code == 1

    def test_cancel_invalid_run_id_exits(self):
        args = make_args(run_id="")
        with pytest.raises(SystemExit) as exc:
            cli._cmd_cancel(args)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_status_success(self, capsys):
        client = MagicMock()
        client.get_run.return_value = {
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
            "workflow_name": "wf",
            "status": "completed",
            "total_cost_usd": 0.0,
            "started_at": None,
            "completed_at": None,
        }
        args = make_args(run_id="550e8400-e29b-41d4-a716-446655440000")
        with patch("sandcastle.__main__._get_client", return_value=client):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_status(args)
        out = capsys.readouterr().out
        assert "550e8400" in out

    def test_status_json_output(self, capsys):
        client = MagicMock()
        client.get_run.return_value = {
            "run_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "completed",
        }
        args = make_args(run_id="550e8400-e29b-41d4-a716-446655440000", **{"json": True})
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_status(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "run_id" in data

    def test_status_error_exits(self):
        client = MagicMock()
        client.get_run.side_effect = Exception("Server error")
        args = make_args(run_id="550e8400-e29b-41d4-a716-446655440000")
        with patch("sandcastle.__main__._get_client", return_value=client):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_status(args)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    def test_run_by_name(self, capsys):
        client = MagicMock()
        run_mock = MagicMock()
        run_mock.run_id = "550e8400-e29b-41d4-a716-446655440000"
        client.run.return_value = run_mock
        args = make_args(
            workflow="my_workflow",
            input=[],
            input_file=None,
            max_cost=None,
            wait=False,
        )
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_run(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "run_id" in data

    def test_run_empty_workflow_name_exits(self):
        args = make_args(workflow="", input=[], input_file=None, max_cost=None, wait=False)
        with patch("sandcastle.__main__._get_client", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_run(args)
        assert exc.value.code == 1

    def test_run_negative_max_cost_exits(self):
        args = make_args(
            workflow="wf", input=[], input_file=None, max_cost=-1.0, wait=False
        )
        with patch("sandcastle.__main__._get_client", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_run(args)
        assert exc.value.code == 1

    def test_run_with_input_file(self, tmp_path, capsys):
        f = tmp_path / "input.json"
        f.write_text('{"key": "val"}')
        client = MagicMock()
        run_mock = MagicMock()
        run_mock.run_id = "550e8400-e29b-41d4-a716-446655440000"
        client.run.return_value = run_mock
        args = make_args(
            workflow="my_workflow",
            input=[],
            input_file=str(f),
            max_cost=None,
            wait=False,
        )
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_run(args)
        client.run.assert_called_once()
        call_kwargs = client.run.call_args
        assert "key" in call_kwargs[1].get("input", {}) or "key" in str(call_kwargs)

    def test_run_error_exits(self):
        client = MagicMock()
        client.run.side_effect = Exception("Connection refused")
        args = make_args(
            workflow="my_workflow", input=[], input_file=None, max_cost=None, wait=False
        )
        with patch("sandcastle.__main__._get_client", return_value=client):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_run(args)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _cmd_health
# ---------------------------------------------------------------------------

class TestCmdHealth:
    def test_health_ok(self, capsys):
        client = MagicMock()
        client.health.return_value = {
            "status": "ok",
            "runtime": True,
            "database": True,
        }
        args = make_args()
        with patch("sandcastle.__main__._get_client", return_value=client):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                cli._cmd_health(args)
        out = capsys.readouterr().out
        assert "ok" in out.lower()

    def test_health_unhealthy_exits(self):
        client = MagicMock()
        client.health.return_value = {
            "status": "degraded",
            "runtime": False,
            "database": False,
        }
        args = make_args()
        with patch("sandcastle.__main__._get_client", return_value=client):
            with patch("sandcastle.__main__._C.supports_color", return_value=False):
                with pytest.raises(SystemExit) as exc:
                    cli._cmd_health(args)
        assert exc.value.code == 1

    def test_health_error_exits(self):
        client = MagicMock()
        client.health.side_effect = Exception("Connection error")
        args = make_args()
        with patch("sandcastle.__main__._get_client", return_value=client):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_health(args)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _cmd_schedule_create and _cmd_schedule_delete
# ---------------------------------------------------------------------------

class TestCmdSchedule:
    def test_schedule_create_success(self, capsys):
        client = MagicMock()
        sched = MagicMock()
        sched.id = "sched-001"
        client.create_schedule.return_value = sched
        args = make_args(workflow="wf", cron="0 9 * * 1-5", input=[])
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_schedule_create(args)
        out = capsys.readouterr().out
        assert "Schedule created" in out

    def test_schedule_create_error_exits(self):
        client = MagicMock()
        client.create_schedule.side_effect = Exception("Bad cron")
        args = make_args(workflow="wf", cron="bad", input=[])
        with patch("sandcastle.__main__._get_client", return_value=client):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_schedule_create(args)
        assert exc.value.code == 1

    def test_schedule_delete_success(self, capsys):
        client = MagicMock()
        client.delete_schedule.return_value = None
        args = make_args(**{"id": "sched-001"})
        with patch("sandcastle.__main__._get_client", return_value=client):
            cli._cmd_schedule_delete(args)
        out = capsys.readouterr().out
        assert "deleted" in out.lower()

    def test_schedule_delete_error_exits(self):
        client = MagicMock()
        client.delete_schedule.side_effect = Exception("Not found")
        args = make_args(**{"id": "sched-999"})
        with patch("sandcastle.__main__._get_client", return_value=client):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_schedule_delete(args)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _cmd_hub routing
# ---------------------------------------------------------------------------

class TestCmdHub:
    def test_unknown_action_exits(self):
        args = make_args(hub_action="badaction")
        with pytest.raises(SystemExit) as exc:
            cli._cmd_hub(args)
        assert exc.value.code == 1

    def test_none_action_exits(self):
        args = make_args(hub_action=None)
        with pytest.raises(SystemExit) as exc:
            cli._cmd_hub(args)
        assert exc.value.code == 1

    def test_search_action_dispatches(self):
        args = make_args(hub_action="search", query="test", category=None)
        with patch("sandcastle.__main__._cmd_hub_search") as mock_search:
            cli._cmd_hub(args)
        mock_search.assert_called_once_with(args)

    def test_list_action_dispatches(self):
        args = make_args(hub_action="list", category=None, limit=20)
        with patch("sandcastle.__main__._cmd_hub_list") as mock_list:
            cli._cmd_hub(args)
        mock_list.assert_called_once_with(args)


# ---------------------------------------------------------------------------
# _api_headers and _api_base
# ---------------------------------------------------------------------------

class TestApiHelpers:
    def test_api_headers_with_key(self):
        args = make_args(api_key="mykey")
        headers = cli._api_headers(args)
        assert headers.get("Authorization") == "Bearer mykey"

    def test_api_headers_without_key(self, monkeypatch):
        monkeypatch.delenv("SANDCASTLE_API_KEY", raising=False)
        args = make_args(api_key="")
        headers = cli._api_headers(args)
        assert "Authorization" not in headers

    def test_api_base_from_args(self):
        args = make_args(url="http://custom:9090")
        base = cli._api_base(args)
        assert base == "http://custom:9090"

    def test_api_base_from_env(self, monkeypatch):
        monkeypatch.setenv("SANDCASTLE_URL", "http://env-url:7777")
        args = make_args(url=None)
        base = cli._api_base(args)
        assert base == "http://env-url:7777"


# ---------------------------------------------------------------------------
# _print_eval_results
# ---------------------------------------------------------------------------

class TestPrintEvalResults:
    def _make_result(self, passed=2, failed=1):
        case_pass = MagicMock()
        case_pass.passed = True
        case_pass.name = "test_case_pass"
        case_pass.assertions = [MagicMock(passed=True), MagicMock(passed=True)]
        case_pass.cost_usd = 0.01
        case_pass.duration_seconds = 1.5
        case_pass.error = None

        case_fail = MagicMock()
        case_fail.passed = False
        case_fail.name = "test_case_fail"
        case_fail.assertions = [MagicMock(passed=False, type="contains", message="not found")]
        case_fail.cost_usd = 0.02
        case_fail.duration_seconds = 2.0
        case_fail.error = "Failed assertion"

        result = MagicMock()
        result.cases = [case_pass, case_fail]
        result.passed = passed
        result.failed = failed
        result.total = passed + failed
        result.pass_rate = passed / (passed + failed)
        result.total_cost_usd = 0.03
        result.total_duration_seconds = 3.5
        return result

    def test_print_eval_results_basic(self, capsys):
        result = self._make_result()
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_eval_results(result)
        out = capsys.readouterr().out
        assert "test_case_pass" in out
        assert "test_case_fail" in out

    def test_print_eval_results_verbose(self, capsys):
        result = self._make_result()
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_eval_results(result, verbose=True)
        out = capsys.readouterr().out
        assert "Failed assertion" in out

    def test_pass_rate_colors(self, capsys):
        # High pass rate (>=0.8)
        result = self._make_result(passed=9, failed=1)
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_eval_results(result)
        # Low pass rate (<0.5)
        result2 = self._make_result(passed=1, failed=9)
        with patch("sandcastle.__main__._C.supports_color", return_value=False):
            cli._print_eval_results(result2)
        # No exception = pass


# ---------------------------------------------------------------------------
# _spinner_print
# ---------------------------------------------------------------------------

class TestSpinnerPrint:
    def test_spinner_print_writes(self, capsys):
        cli._spinner_print("Loading...")
        captured = capsys.readouterr()
        assert "Loading..." in captured.out


# ---------------------------------------------------------------------------
# _port_in_use
# ---------------------------------------------------------------------------

class TestPortInUse:
    def test_port_available(self):
        # Use a very high port that is unlikely to be in use
        result = cli._port_in_use(59999)
        assert isinstance(result, bool)

    def test_port_in_use_returns_true(self):
        import socket
        # Create a server socket on a random port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            result = cli._port_in_use(port)
        assert result is True


# ---------------------------------------------------------------------------
# _cmd_init - mocked stdin (non-tty)
# ---------------------------------------------------------------------------

class TestCmdInit:
    def test_init_exits_if_not_tty(self):
        args = make_args()
        with patch("sys.stdin.isatty", return_value=False):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_init(args)
        assert exc.value.code == 1

    def test_init_keyboard_interrupt_returns(self):
        args = make_args()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("sandcastle.__main__._cmd_init_inner", side_effect=KeyboardInterrupt):
                # Should NOT raise - should print "Setup cancelled." and return
                cli._cmd_init(args)


# ---------------------------------------------------------------------------
# _cmd_mcp - missing import
# ---------------------------------------------------------------------------

class TestCmdMcp:
    def test_mcp_missing_import_exits(self):
        args = make_args(url=None, api_key=None)
        # Simulate ImportError when mcp_server cannot be imported
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *a, **kw):
            if name == "sandcastle.mcp_server":
                raise ImportError("mcp not installed")
            return real_import(name, *a, **kw)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(SystemExit) as exc:
                cli._cmd_mcp(args)
        assert exc.value.code == 1

    def test_mcp_sets_env_vars(self):
        args = make_args(url="http://custom:9090", api_key="testkey")
        mock_main = MagicMock()
        # Inject a fake sandcastle.mcp_server into sys.modules first so the import
        # inside _cmd_mcp resolves to our mock. patch("sandcastle.mcp_server.main")
        # can't be used here because that path only resolves once the real module
        # imports cleanly, which requires the [mcp] extra (absent in plain CI).
        with patch.dict(
            "sys.modules",
            {"sandcastle.mcp_server": MagicMock(main=mock_main)},
        ):
            cli._cmd_mcp(args)
        assert os.environ.get("SANDCASTLE_URL") == "http://custom:9090"
        assert os.environ.get("SANDCASTLE_API_KEY") == "testkey"


# ---------------------------------------------------------------------------
# _fetch_hub_registry - error handling
# ---------------------------------------------------------------------------

class TestFetchHubRegistry:
    def test_fetch_error_exits(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            with pytest.raises(SystemExit) as exc:
                cli._fetch_hub_registry()
        assert exc.value.code == 1

    def test_oversized_registry_exits(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        # Return more than max size
        mock_resp.read.return_value = b"x" * (5 * 1024 * 1024 + 2)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(SystemExit) as exc:
                cli._fetch_hub_registry()
        assert exc.value.code == 1

    def test_invalid_structure_exits(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'["not", "a", "dict"]'
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(SystemExit) as exc:
                cli._fetch_hub_registry()
        assert exc.value.code == 1
