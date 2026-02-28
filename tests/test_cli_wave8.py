"""Wave 8 CLI audit: deep tests for input validation, error handling,
edge cases, and security across all CLI commands in __main__.py."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from sandcastle.__main__ import (
    _build_parser,
    _C,
    _color,
    _cmd_approve,
    _cmd_cancel,
    _cmd_doctor,
    _cmd_eval,
    _cmd_fork,
    _cmd_generate,
    _cmd_health,
    _cmd_hub,
    _cmd_hub_install,
    _cmd_hub_list,
    _cmd_hub_search,
    _cmd_keys,
    _cmd_keys_create,
    _cmd_keys_delete,
    _cmd_keys_rotate,
    _cmd_logs,
    _cmd_ls,
    _cmd_mcp,
    _cmd_reject,
    _cmd_replay,
    _cmd_run,
    _cmd_runs,
    _cmd_runs_compare,
    _cmd_serve,
    _cmd_status,
    _cmd_templates,
    _cmd_worker,
    _find_pending_approval,
    _fmt_time,
    _format_cli_error,
    _load_dot_env,
    _load_input_file,
    _MAX_INPUT_FILE_SIZE,
    _parse_input_pairs,
    _print_run_detail,
    _sanitize_hub_filename,
    _status_color,
    _table,
    _to_dict,
    _to_dicts,
    _validate_hub_download_url,
    _validate_hub_yaml,
    _validate_run_id,
    _wait_for_run,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns(**kwargs) -> argparse.Namespace:
    """Create an argparse.Namespace with given attributes."""
    return argparse.Namespace(**kwargs)


# ---------------------------------------------------------------------------
# _parse_input_pairs - edge cases
# ---------------------------------------------------------------------------


class TestParseInputPairs:
    def test_empty_list(self):
        assert _parse_input_pairs([]) == {}

    def test_none(self):
        assert _parse_input_pairs(None) == {}

    def test_simple_pairs(self):
        result = _parse_input_pairs(["name=test", "count=5"])
        assert result == {"name": "test", "count": 5}

    def test_json_value(self):
        result = _parse_input_pairs(['data={"a": 1}'])
        assert result == {"data": {"a": 1}}

    def test_value_with_equals(self):
        """Values containing '=' should preserve them after the first split."""
        result = _parse_input_pairs(["formula=a=b+c"])
        assert result == {"formula": "a=b+c"}

    def test_no_equals_exits(self):
        with pytest.raises(SystemExit):
            _parse_input_pairs(["no-equals-here"])

    def test_empty_key_exits(self):
        """An input like '=value' should reject the empty key."""
        with pytest.raises(SystemExit):
            _parse_input_pairs(["=value"])

    def test_whitespace_only_key_exits(self):
        """An input like '  =value' should reject the whitespace-only key."""
        with pytest.raises(SystemExit):
            _parse_input_pairs(["  =value"])

    def test_empty_value_is_valid(self):
        """'key=' should produce key -> empty string."""
        result = _parse_input_pairs(["key="])
        assert result == {"key": ""}

    def test_boolean_json_value(self):
        result = _parse_input_pairs(["flag=true"])
        assert result == {"flag": True}

    def test_array_json_value(self):
        result = _parse_input_pairs(['items=[1, 2, 3]'])
        assert result == {"items": [1, 2, 3]}

    def test_null_json_value(self):
        result = _parse_input_pairs(["val=null"])
        assert result == {"val": None}


# ---------------------------------------------------------------------------
# _validate_run_id - edge cases
# ---------------------------------------------------------------------------


class TestValidateRunId:
    def test_valid_uuid(self):
        """Standard UUID should pass without error."""
        _validate_run_id("550e8400-e29b-41d4-a716-446655440000")

    def test_empty_string_exits(self):
        with pytest.raises(SystemExit):
            _validate_run_id("")

    def test_whitespace_only_exits(self):
        with pytest.raises(SystemExit):
            _validate_run_id("   ")

    def test_path_traversal_slash_exits(self):
        with pytest.raises(SystemExit):
            _validate_run_id("../etc/passwd")

    def test_backslash_exits(self):
        with pytest.raises(SystemExit):
            _validate_run_id("foo\\bar")

    def test_space_exits(self):
        with pytest.raises(SystemExit):
            _validate_run_id("foo bar")

    def test_null_byte_exits(self):
        """Null bytes should be blocked to prevent injection."""
        with pytest.raises(SystemExit):
            _validate_run_id("abc\x00def")

    def test_control_char_exits(self):
        """Control characters (tab, newline, etc.) should be blocked."""
        with pytest.raises(SystemExit):
            _validate_run_id("abc\ndef")

    def test_tab_char_exits(self):
        with pytest.raises(SystemExit):
            _validate_run_id("abc\tdef")

    def test_non_uuid_warns(self, capsys):
        """Non-UUID strings should print a warning but NOT exit."""
        _validate_run_id("not-a-uuid-format")
        captured = capsys.readouterr()
        assert "Warning" in captured.err


# ---------------------------------------------------------------------------
# _table - formatting edge cases
# ---------------------------------------------------------------------------


class TestTable:
    def test_empty_rows(self):
        assert _table(["A", "B"], []) == "(no data)"

    def test_basic_table(self):
        result = _table(["Name"], [["Alice"]])
        assert "Alice" in result

    def test_truncation(self):
        """Long values should be truncated with ellipsis."""
        long_val = "x" * 100
        result = _table(["Col"], [[long_val]], max_col=10)
        assert len(long_val) > 10
        # Truncated with ellipsis character
        assert "\u2026" in result

    def test_rows_shorter_than_headers(self):
        """Rows with fewer columns than headers should be padded."""
        result = _table(["A", "B", "C"], [["only-one"]])
        assert "only-one" in result

    def test_rows_longer_than_headers(self):
        """Rows with more columns than headers should be truncated, not crash."""
        result = _table(["A"], [["val1", "extra1", "extra2"]])
        assert "val1" in result
        # Extra columns should be silently dropped
        assert "extra1" not in result

    def test_single_row_single_col(self):
        result = _table(["X"], [["hello"]])
        assert "hello" in result

    def test_unicode_content(self):
        """Unicode content should not crash the table formatter."""
        result = _table(["Name"], [["Tomas Pflanzer"]])
        assert "Tomas" in result


# ---------------------------------------------------------------------------
# _load_input_file - edge cases
# ---------------------------------------------------------------------------


class TestLoadInputFile:
    def test_valid_json_file(self, tmp_path):
        p = tmp_path / "input.json"
        p.write_text('{"key": "value"}')
        result = _load_input_file(str(p))
        assert result == {"key": "value"}

    def test_file_not_found_exits(self):
        with pytest.raises(SystemExit):
            _load_input_file("/nonexistent/file.json")

    def test_invalid_json_exits(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json at all {{{")
        with pytest.raises(SystemExit):
            _load_input_file(str(p))

    def test_json_array_not_dict_exits(self, tmp_path):
        """Input file must contain a JSON object, not an array."""
        p = tmp_path / "arr.json"
        p.write_text("[1, 2, 3]")
        with pytest.raises(SystemExit):
            _load_input_file(str(p))

    def test_json_string_not_dict_exits(self, tmp_path):
        p = tmp_path / "str.json"
        p.write_text('"just a string"')
        with pytest.raises(SystemExit):
            _load_input_file(str(p))

    def test_oversized_file_exits(self, tmp_path):
        """Files exceeding _MAX_INPUT_FILE_SIZE should be rejected."""
        p = tmp_path / "huge.json"
        # Create a file just over the limit
        p.write_text("{" + '"x": "' + "a" * (_MAX_INPUT_FILE_SIZE + 100) + '"' + "}")
        with pytest.raises(SystemExit):
            _load_input_file(str(p))

    def test_empty_json_object(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("{}")
        result = _load_input_file(str(p))
        assert result == {}


# ---------------------------------------------------------------------------
# _load_dot_env - edge cases
# ---------------------------------------------------------------------------


class TestLoadDotEnv:
    def test_no_env_file(self, tmp_path, monkeypatch):
        """No .env file should be a no-op."""
        monkeypatch.chdir(tmp_path)
        # Reset the loaded flag
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        _load_dot_env()
        # Should not crash

    def test_env_file_loads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_CLI_WAVE8_VAR=hello123\n")
        # Ensure not already set
        monkeypatch.delenv("TEST_CLI_WAVE8_VAR", raising=False)
        _load_dot_env()
        assert os.environ.get("TEST_CLI_WAVE8_VAR") == "hello123"
        # Cleanup
        monkeypatch.delenv("TEST_CLI_WAVE8_VAR", raising=False)

    def test_env_existing_var_not_overwritten(self, tmp_path, monkeypatch):
        """Env vars already set should NOT be overwritten by .env."""
        monkeypatch.chdir(tmp_path)
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_CLI_WAVE8_PRIO=fromfile\n")
        monkeypatch.setenv("TEST_CLI_WAVE8_PRIO", "fromenv")
        _load_dot_env()
        assert os.environ.get("TEST_CLI_WAVE8_PRIO") == "fromenv"

    def test_env_with_export_prefix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        env_file = tmp_path / ".env"
        env_file.write_text("export TEST_CLI_WAVE8_EXP=exported\n")
        monkeypatch.delenv("TEST_CLI_WAVE8_EXP", raising=False)
        _load_dot_env()
        assert os.environ.get("TEST_CLI_WAVE8_EXP") == "exported"
        monkeypatch.delenv("TEST_CLI_WAVE8_EXP", raising=False)

    def test_env_quoted_values(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        env_file = tmp_path / ".env"
        env_file.write_text('TEST_CLI_WAVE8_Q="quoted value"\n')
        monkeypatch.delenv("TEST_CLI_WAVE8_Q", raising=False)
        _load_dot_env()
        assert os.environ.get("TEST_CLI_WAVE8_Q") == "quoted value"
        monkeypatch.delenv("TEST_CLI_WAVE8_Q", raising=False)

    def test_env_comments_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        env_file = tmp_path / ".env"
        env_file.write_text("# Comment line\nTEST_CLI_WAVE8_CMT=ok\n")
        monkeypatch.delenv("TEST_CLI_WAVE8_CMT", raising=False)
        _load_dot_env()
        assert os.environ.get("TEST_CLI_WAVE8_CMT") == "ok"
        monkeypatch.delenv("TEST_CLI_WAVE8_CMT", raising=False)

    def test_idempotent(self, tmp_path, monkeypatch):
        """Loading .env twice should be idempotent (second call is a no-op)."""
        monkeypatch.chdir(tmp_path)
        import sandcastle.__main__ as m
        m._dot_env_loaded = False
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_CLI_WAVE8_IDP=first\n")
        monkeypatch.delenv("TEST_CLI_WAVE8_IDP", raising=False)
        _load_dot_env()
        assert os.environ.get("TEST_CLI_WAVE8_IDP") == "first"
        # Rewrite .env with different value
        env_file.write_text("TEST_CLI_WAVE8_IDP=second\n")
        _load_dot_env()
        # Should still be "first" because _dot_env_loaded prevents re-read
        assert os.environ.get("TEST_CLI_WAVE8_IDP") == "first"
        monkeypatch.delenv("TEST_CLI_WAVE8_IDP", raising=False)


# ---------------------------------------------------------------------------
# _sanitize_hub_filename - security
# ---------------------------------------------------------------------------


class TestSanitizeHubFilename:
    def test_simple_slug(self):
        assert _sanitize_hub_filename("my-workflow") == "my-workflow.yaml"

    def test_path_traversal(self):
        result = _sanitize_hub_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert result.endswith(".yaml")

    def test_slash_in_slug(self):
        """Only the last path component should be used."""
        result = _sanitize_hub_filename("author/my-workflow")
        assert result == "my-workflow.yaml"

    def test_special_characters_sanitized(self):
        result = _sanitize_hub_filename("my workflow!@#$%")
        assert " " not in result
        assert "!" not in result
        assert result.endswith(".yaml")

    def test_dot_prefix_stripped(self):
        """Hidden file names (starting with .) should be stripped."""
        result = _sanitize_hub_filename(".hidden")
        assert not result.startswith(".")

    def test_empty_after_sanitize(self):
        """If slug is only dots (which get sanitized to underscores then stripped),
        fallback to 'workflow.yaml'."""
        # Dots get converted to underscores by the regex, so "..." -> "___"
        # which is valid. The fallback only triggers when result is truly empty.
        result = _sanitize_hub_filename("...")
        assert result.endswith(".yaml")
        assert len(result) > len(".yaml")  # Not empty base name

    def test_truly_empty_slug(self):
        """If slug is only path separators, last component is empty -> fallback."""
        result = _sanitize_hub_filename("/")
        assert result == "workflow.yaml"

    def test_null_bytes_sanitized(self):
        result = _sanitize_hub_filename("file\x00name")
        assert "\x00" not in result
        assert result.endswith(".yaml")


# ---------------------------------------------------------------------------
# _validate_hub_download_url - security
# ---------------------------------------------------------------------------


class TestValidateHubDownloadUrl:
    def test_valid_github_raw(self):
        assert _validate_hub_download_url(
            "https://raw.githubusercontent.com/user/repo/main/file.yaml"
        )

    def test_http_rejected(self):
        assert not _validate_hub_download_url(
            "http://raw.githubusercontent.com/user/repo/main/file.yaml"
        )

    def test_untrusted_host(self):
        assert not _validate_hub_download_url(
            "https://evil.com/malicious.yaml"
        )

    def test_github_dot_com_valid(self):
        assert _validate_hub_download_url(
            "https://github.com/user/repo/raw/main/file.yaml"
        )

    def test_ftp_scheme_rejected(self):
        assert not _validate_hub_download_url(
            "ftp://raw.githubusercontent.com/file.yaml"
        )

    def test_empty_url(self):
        assert not _validate_hub_download_url("")

    def test_javascript_scheme(self):
        assert not _validate_hub_download_url("javascript:alert(1)")


# ---------------------------------------------------------------------------
# _validate_hub_yaml - content validation
# ---------------------------------------------------------------------------


class TestValidateHubYaml:
    def test_valid_yaml(self):
        yaml_str = "name: test\nsteps:\n  - id: s1\n    type: llm\n"
        valid, msg = _validate_hub_yaml(yaml_str)
        assert valid
        assert msg == ""

    def test_no_name_field(self):
        yaml_str = "steps:\n  - id: s1\n"
        valid, msg = _validate_hub_yaml(yaml_str)
        assert not valid
        assert "name" in msg

    def test_no_steps_field(self):
        yaml_str = "name: test\n"
        valid, msg = _validate_hub_yaml(yaml_str)
        assert not valid
        assert "steps" in msg

    def test_steps_not_list(self):
        yaml_str = "name: test\nsteps: not-a-list\n"
        valid, msg = _validate_hub_yaml(yaml_str)
        assert not valid

    def test_invalid_yaml_syntax(self):
        yaml_str = "name: test\nsteps:\n  - {bad yaml {{"
        valid, msg = _validate_hub_yaml(yaml_str)
        assert not valid
        assert "Invalid YAML" in msg

    def test_not_mapping(self):
        yaml_str = "- item1\n- item2\n"
        valid, msg = _validate_hub_yaml(yaml_str)
        assert not valid
        assert "mapping" in msg

    def test_oversized_content(self):
        yaml_str = "name: test\nsteps:\n  - id: s1\n" + "x" * (512 * 1024 + 1)
        valid, msg = _validate_hub_yaml(yaml_str)
        assert not valid
        assert "too large" in msg


# ---------------------------------------------------------------------------
# _format_cli_error - error formatting
# ---------------------------------------------------------------------------


class TestFormatCliError:
    def test_sandcastle_error_401(self):
        from sandcastle.sdk import SandcastleError
        err = SandcastleError(401, "UNAUTHORIZED", "Bad key")
        msg = _format_cli_error(err)
        assert "401" in msg
        assert "Authentication" in msg

    def test_sandcastle_error_403(self):
        from sandcastle.sdk import SandcastleError
        err = SandcastleError(403, "FORBIDDEN", "No access")
        msg = _format_cli_error(err)
        assert "403" in msg
        assert "denied" in msg

    def test_sandcastle_error_404(self):
        from sandcastle.sdk import SandcastleError
        err = SandcastleError(404, "NOT_FOUND", "Run not found")
        msg = _format_cli_error(err)
        assert "404" in msg

    def test_sandcastle_error_422(self):
        from sandcastle.sdk import SandcastleError
        err = SandcastleError(422, "VALIDATION", "Bad input")
        msg = _format_cli_error(err)
        assert "422" in msg

    def test_sandcastle_error_500(self):
        from sandcastle.sdk import SandcastleError
        err = SandcastleError(500, "INTERNAL", "Server crash")
        msg = _format_cli_error(err)
        assert "500" in msg

    def test_connection_error(self):
        # Use a class with "ConnectError" in its name
        class ConnectError(Exception):
            pass
        err = ConnectError("could not reach host")
        msg = _format_cli_error(err)
        assert "Connection failed" in msg

    def test_file_not_found(self):
        err = FileNotFoundError("missing.yaml")
        msg = _format_cli_error(err)
        assert "File not found" in msg

    def test_permission_error(self):
        err = PermissionError("cannot read /etc/shadow")
        msg = _format_cli_error(err)
        assert "Permission denied" in msg

    def test_generic_error(self):
        err = RuntimeError("something went wrong")
        msg = _format_cli_error(err)
        assert "Error:" in msg


# ---------------------------------------------------------------------------
# _status_color - coverage
# ---------------------------------------------------------------------------


class TestStatusColor:
    def test_completed(self):
        # Just ensure it doesn't crash, returns string
        assert isinstance(_status_color("completed"), str)

    def test_failed(self):
        assert isinstance(_status_color("failed"), str)

    def test_running(self):
        assert isinstance(_status_color("running"), str)

    def test_queued(self):
        assert isinstance(_status_color("queued"), str)

    def test_unknown(self):
        assert _status_color("weird") == "weird"


# ---------------------------------------------------------------------------
# _to_dict / _to_dicts - edge cases
# ---------------------------------------------------------------------------


class TestToDict:
    def test_dict_passthrough(self):
        assert _to_dict({"a": 1}) == {"a": 1}

    def test_object_with_dict(self):
        obj = type("Obj", (), {})()
        obj.x = 42
        result = _to_dict(obj)
        assert result["x"] == 42

    def test_pydantic_model(self):
        m = MagicMock()
        m.model_dump.return_value = {"key": "val"}
        result = _to_dict(m)
        assert result == {"key": "val"}

    def test_plain_value(self):
        result = _to_dict(42)
        assert result == {"value": "42"}


class TestToDicts:
    def test_list_of_dicts(self):
        result = _to_dicts([{"a": 1}, {"b": 2}])
        assert len(result) == 2

    def test_paginated_list(self):
        mock = MagicMock()
        mock.items = [{"a": 1}]
        result = _to_dicts(mock)
        assert len(result) == 1

    def test_wrapped_data(self):
        result = _to_dicts({"data": [{"x": 1}]})
        assert len(result) == 1

    def test_empty_list(self):
        assert _to_dicts([]) == []

    def test_none_like(self):
        """Non-list, non-dict, no .items -> empty list."""
        assert _to_dicts(42) == []


# ---------------------------------------------------------------------------
# _fmt_time - edge cases
# ---------------------------------------------------------------------------


class TestFmtTime:
    def test_none(self):
        assert _fmt_time(None) == "-"

    def test_iso_string(self):
        result = _fmt_time("2026-02-28T15:30:00Z")
        assert "2026-02-28 15:30" in result

    def test_datetime_object(self):
        from datetime import datetime
        dt = datetime(2026, 2, 28, 12, 0)
        result = _fmt_time(dt)
        assert "2026-02-28" in result

    def test_integer_fallback(self):
        result = _fmt_time(12345)
        assert result == "12345"


# ---------------------------------------------------------------------------
# _cmd_run - validation
# ---------------------------------------------------------------------------


class TestCmdRunValidation:
    def test_negative_max_cost_exits(self):
        args = _ns(
            workflow="test-wf", input=None, input_file=None,
            wait=False, max_cost=-5.0, json=False,
            url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_run(args)

    def test_empty_workflow_name_exits(self):
        args = _ns(
            workflow="", input=None, input_file=None,
            wait=False, max_cost=None, json=False,
            url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_run(args)

    def test_whitespace_workflow_name_exits(self):
        args = _ns(
            workflow="   ", input=None, input_file=None,
            wait=False, max_cost=None, json=False,
            url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_run(args)

    def test_zero_max_cost_is_valid(self):
        """Zero should be valid (effectively no budget)."""
        args = _ns(
            workflow="test-wf", input=None, input_file=None,
            wait=False, max_cost=0.0, json=False,
            url=None, api_key=None,
        )
        mock_client = MagicMock()
        mock_client.run.return_value = MagicMock(run_id="abc-123")
        with patch("sandcastle.__main__._get_client", return_value=mock_client):
            _cmd_run(args)


# ---------------------------------------------------------------------------
# _cmd_serve - port validation
# ---------------------------------------------------------------------------


class TestCmdServeValidation:
    def test_port_zero_exits(self):
        args = _ns(port=0, host="0.0.0.0", reload=False)
        with pytest.raises(SystemExit):
            _cmd_serve(args)

    def test_port_negative_exits(self):
        args = _ns(port=-1, host="0.0.0.0", reload=False)
        with pytest.raises(SystemExit):
            _cmd_serve(args)

    def test_port_too_large_exits(self):
        args = _ns(port=70000, host="0.0.0.0", reload=False)
        with pytest.raises(SystemExit):
            _cmd_serve(args)


# ---------------------------------------------------------------------------
# _cmd_runs / _ls_runs - limit validation
# ---------------------------------------------------------------------------


class TestRunsLimitValidation:
    def test_cmd_runs_negative_limit_exits(self):
        args = _ns(
            url=None, api_key=None, limit=-1,
            status=None, json=False, runs_action=None,
        )
        with pytest.raises(SystemExit):
            _cmd_runs(args)

    def test_cmd_runs_zero_limit_exits(self):
        args = _ns(
            url=None, api_key=None, limit=0,
            status=None, json=False, runs_action=None,
        )
        with pytest.raises(SystemExit):
            _cmd_runs(args)

    def test_ls_runs_negative_limit_exits(self):
        mock_client = MagicMock()
        args = _ns(status=None, limit=-1)
        with pytest.raises(SystemExit):
            from sandcastle.__main__ import _ls_runs
            _ls_runs(mock_client, args)


# ---------------------------------------------------------------------------
# _cmd_hub_list - limit validation
# ---------------------------------------------------------------------------


class TestHubListLimitValidation:
    @patch("sandcastle.__main__._fetch_hub_registry")
    def test_negative_limit_exits(self, mock_fetch):
        mock_fetch.return_value = {"templates": []}
        args = _ns(category=None, limit=-1, json=False)
        with pytest.raises(SystemExit):
            _cmd_hub_list(args)


# ---------------------------------------------------------------------------
# _cmd_hub_search - validation
# ---------------------------------------------------------------------------


class TestHubSearchValidation:
    @patch("sandcastle.__main__._fetch_hub_registry")
    def test_empty_query_exits(self, mock_fetch):
        mock_fetch.return_value = {"templates": []}
        args = _ns(query="", category=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_hub_search(args)

    @patch("sandcastle.__main__._fetch_hub_registry")
    def test_whitespace_query_exits(self, mock_fetch):
        mock_fetch.return_value = {"templates": []}
        args = _ns(query="   ", category=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_hub_search(args)


# ---------------------------------------------------------------------------
# _cmd_hub_install - overwrite protection
# ---------------------------------------------------------------------------


class TestHubInstallOverwrite:
    @patch("sandcastle.__main__._fetch_hub_registry")
    def test_existing_file_no_force_non_tty_exits(self, mock_fetch, tmp_path):
        """If target file exists, --force not set, and stdin is not tty, should exit."""
        from sandcastle.engine.hub_scanner import ScanResult

        mock_fetch.return_value = {
            "templates": [{
                "slug": "test-wf",
                "name": "Test",
                "download_url": "https://raw.githubusercontent.com/user/repo/main/wf.yaml",
                "sha256": None,
                "step_count": 2,
                "category": "test",
            }]
        }
        # Create existing file
        target_dir = tmp_path / "workflows"
        target_dir.mkdir()
        (target_dir / "test-wf.yaml").write_text("existing content")

        yaml_content = "name: test\nsteps:\n  - id: s1\n    type: llm\n"

        args = _ns(
            slug="test-wf", dir=str(target_dir), force=False,
            json=False, url=None, api_key=None,
        )

        mock_resp_ctx = MagicMock()
        mock_resp_ctx.read.return_value = yaml_content.encode()
        mock_resp_ctx.geturl.return_value = "https://raw.githubusercontent.com/user/repo/main/wf.yaml"
        mock_resp_ctx.__enter__ = lambda s: s
        mock_resp_ctx.__exit__ = MagicMock(return_value=False)

        empty_scan = ScanResult(safe=True, errors=[], warnings=[])

        with patch("urllib.request.urlopen", return_value=mock_resp_ctx), \
             patch("sandcastle.engine.hub_scanner.scan_template", return_value=empty_scan), \
             patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False

            with pytest.raises(SystemExit):
                _cmd_hub_install(args)


# ---------------------------------------------------------------------------
# _find_pending_approval - error handling
# ---------------------------------------------------------------------------


class TestFindPendingApproval:
    def test_network_error_exits(self):
        """Network errors should now exit with error, not return None silently."""
        with patch("httpx.get", side_effect=Exception("network error")):
            with pytest.raises(SystemExit):
                _find_pending_approval(
                    "http://localhost:8080", {}, "test-run-id"
                )

    def test_no_matching_approval_returns_none(self):
        """If no matching approval, should return None."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "appr-1", "run_id": "other-run-id"}
            ]
        }
        with patch("httpx.get", return_value=mock_resp):
            result = _find_pending_approval(
                "http://localhost:8080", {}, "my-run-id"
            )
            assert result is None

    def test_matching_approval_found(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "appr-1", "run_id": "my-run-id"}
            ]
        }
        with patch("httpx.get", return_value=mock_resp):
            result = _find_pending_approval(
                "http://localhost:8080", {}, "my-run-id"
            )
            assert result == "appr-1"

    def test_empty_data_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        with patch("httpx.get", return_value=mock_resp):
            result = _find_pending_approval(
                "http://localhost:8080", {}, "my-run-id"
            )
            assert result is None


# ---------------------------------------------------------------------------
# _wait_for_run - edge cases
# ---------------------------------------------------------------------------


class TestWaitForRun:
    def test_immediate_completion(self):
        """Run that is already completed should return immediately."""
        mock_client = MagicMock()
        mock_client.get_run.return_value = {
            "id": "run-1", "status": "completed", "outputs": {}
        }
        result = _wait_for_run(mock_client, "run-1")
        assert result["status"] == "completed"

    def test_failed_run(self):
        mock_client = MagicMock()
        mock_client.get_run.return_value = {
            "id": "run-1", "status": "failed", "error": "boom"
        }
        result = _wait_for_run(mock_client, "run-1")
        assert result["status"] == "failed"

    def test_awaiting_approval_is_terminal(self):
        mock_client = MagicMock()
        mock_client.get_run.return_value = {
            "id": "run-1", "status": "awaiting_approval"
        }
        result = _wait_for_run(mock_client, "run-1")
        assert result["status"] == "awaiting_approval"

    def test_keyboard_interrupt(self):
        mock_client = MagicMock()
        mock_client.get_run.side_effect = KeyboardInterrupt()
        result = _wait_for_run(mock_client, "run-1")
        assert result["status"] == "interrupted"


# ---------------------------------------------------------------------------
# _cmd_status - error handling
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_status_with_invalid_run_id_exits(self):
        args = _ns(run_id="", json=False, url=None, api_key=None)
        with pytest.raises(SystemExit):
            _cmd_status(args)

    @patch("sandcastle.__main__._get_client")
    def test_status_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_run.side_effect = Exception("Not found")
        mock_get_client.return_value = mock_client

        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            json=False, url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_status(args)


# ---------------------------------------------------------------------------
# _cmd_cancel - error handling
# ---------------------------------------------------------------------------


class TestCmdCancel:
    def test_cancel_with_traversal_id_exits(self):
        args = _ns(run_id="../etc", json=False, url=None, api_key=None)
        with pytest.raises(SystemExit):
            _cmd_cancel(args)

    @patch("sandcastle.__main__._get_client")
    def test_cancel_success_json(self, mock_get_client, capsys):
        mock_client = MagicMock()
        mock_client.cancel_run.return_value = {"cancelled": True}
        mock_get_client.return_value = mock_client

        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            json=True, url=None, api_key=None,
        )
        _cmd_cancel(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["cancelled"] is True


# ---------------------------------------------------------------------------
# _cmd_approve / _cmd_reject - validation
# ---------------------------------------------------------------------------


class TestCmdApproveReject:
    def test_approve_invalid_run_id_exits(self):
        args = _ns(run_id="", data=None, json=False, url=None, api_key=None)
        with pytest.raises(SystemExit):
            _cmd_approve(args)

    def test_reject_invalid_run_id_exits(self):
        args = _ns(run_id="", reason=None, json=False, url=None, api_key=None)
        with pytest.raises(SystemExit):
            _cmd_reject(args)

    def test_approve_bad_json_data_exits(self):
        """--data with invalid JSON should exit cleanly."""
        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            data="{invalid json",
            json=False, url=None, api_key=None,
        )
        # Mock the approval lookup to return an ID so we reach the JSON parse
        with patch("sandcastle.__main__._find_pending_approval", return_value="appr-1"):
            with pytest.raises(SystemExit):
                _cmd_approve(args)


# ---------------------------------------------------------------------------
# _cmd_replay / _cmd_fork - validation
# ---------------------------------------------------------------------------


class TestCmdReplayFork:
    def test_replay_missing_from_step_exits(self):
        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            from_step=None,
            json=False, url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_replay(args)

    def test_fork_missing_from_step_exits(self):
        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            from_step=None, change=None,
            json=False, url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_fork(args)

    def test_fork_invalid_change_format_exits(self):
        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            from_step="step1",
            change=["no-equals"],
            json=False, url=None, api_key=None,
        )
        with pytest.raises(SystemExit):
            _cmd_fork(args)

    @patch("httpx.post")
    def test_replay_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "new_run_id": "new-123",
                "parent_run_id": "550e8400-e29b-41d4-a716-446655440000",
                "replay_from_step": "step1",
                "status": "queued",
            }
        }
        mock_post.return_value = mock_resp

        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            from_step="step1",
            json=False, url=None, api_key=None,
        )
        _cmd_replay(args)  # Should not raise


# ---------------------------------------------------------------------------
# _cmd_templates - JSON output
# ---------------------------------------------------------------------------


class TestCmdTemplates:
    @patch("httpx.get")
    def test_templates_json_output(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"name": "Template1", "category": "test", "step_count": 3, "description": "desc"}
            ]
        }
        mock_get.return_value = mock_resp

        args = _ns(
            url="http://localhost:8080", api_key=None,
            category=None, json=True,
        )
        _cmd_templates(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["name"] == "Template1"

    @patch("httpx.get")
    def test_templates_empty(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        args = _ns(
            url="http://localhost:8080", api_key=None,
            category=None, json=False,
        )
        _cmd_templates(args)
        out = capsys.readouterr().out
        assert "No templates found" in out

    @patch("httpx.get")
    def test_templates_category_filter(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"name": "T1", "category": "marketing", "step_count": 2, "description": ""},
                {"name": "T2", "category": "devops", "step_count": 1, "description": ""},
            ]
        }
        mock_get.return_value = mock_resp

        args = _ns(
            url="http://localhost:8080", api_key=None,
            category="devops", json=False,
        )
        _cmd_templates(args)
        out = capsys.readouterr().out
        assert "T2" in out
        # marketing template should be filtered out
        assert "T1" not in out


# ---------------------------------------------------------------------------
# _cmd_logs - error handling
# ---------------------------------------------------------------------------


class TestCmdLogs:
    def test_logs_invalid_run_id_exits(self):
        args = _ns(
            run_id="", follow=False,
            url=None, api_key=None, json=False,
        )
        with pytest.raises(SystemExit):
            _cmd_logs(args)


# ---------------------------------------------------------------------------
# _cmd_ls - routing
# ---------------------------------------------------------------------------


class TestCmdLs:
    def test_ls_no_resource_exits(self):
        args = _ns(resource=None, url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_ls(args)

    def test_ls_unknown_resource_exits(self):
        args = _ns(resource="bananas", url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_ls(args)


# ---------------------------------------------------------------------------
# _cmd_keys - routing
# ---------------------------------------------------------------------------


class TestCmdKeys:
    def test_keys_no_action_exits(self):
        args = _ns(keys_action=None, url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_keys(args)


# ---------------------------------------------------------------------------
# _cmd_hub - routing
# ---------------------------------------------------------------------------


class TestCmdHub:
    def test_hub_no_action_exits(self):
        args = _ns(hub_action=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_hub(args)


# ---------------------------------------------------------------------------
# main() - dispatch
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def test_no_command_prints_help(self, capsys):
        with patch("sys.argv", ["sandcastle"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_unknown_command_exits(self, capsys):
        with patch("sys.argv", ["sandcastle", "nonexistent"]):
            with pytest.raises(SystemExit):
                main()

    def test_version_flag(self, capsys):
        with patch("sys.argv", ["sandcastle", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_db_no_subcommand_exits(self):
        with patch("sys.argv", ["sandcastle", "db"]):
            with pytest.raises(SystemExit):
                main()

    def test_schedule_no_subcommand_exits(self):
        with patch("sys.argv", ["sandcastle", "schedule"]):
            with pytest.raises(SystemExit):
                main()


# ---------------------------------------------------------------------------
# Argument parser completeness
# ---------------------------------------------------------------------------


class TestParserCompleteness:
    def test_all_commands_have_parsers(self):
        parser = _build_parser()
        # Verify the key commands are parseable
        for cmd in [
            "init", "serve", "doctor", "worker", "health",
            "templates", "runs",
        ]:
            args = parser.parse_args([cmd])
            assert args.command == cmd

    def test_hub_subcommands(self):
        parser = _build_parser()
        args = parser.parse_args(["hub", "list"])
        assert args.command == "hub"
        assert args.hub_action == "list"

    def test_keys_subcommands(self):
        parser = _build_parser()
        args = parser.parse_args(["keys", "list"])
        assert args.command == "keys"
        assert args.keys_action == "list"

    def test_dlq_subcommands(self):
        parser = _build_parser()
        args = parser.parse_args(["dlq", "list"])
        assert args.command == "dlq"
        assert args.dlq_action == "list"

    def test_violations_subcommands(self):
        parser = _build_parser()
        args = parser.parse_args(["violations", "list"])
        assert args.command == "violations"
        assert args.violations_action == "list"

    def test_tools_subcommands(self):
        parser = _build_parser()
        args = parser.parse_args(["tools", "list"])
        assert args.command == "tools"
        assert args.tools_action == "list"

    def test_autopilot_subcommands(self):
        parser = _build_parser()
        args = parser.parse_args(["autopilot", "list"])
        assert args.command == "autopilot"
        assert args.autopilot_action == "list"

    def test_runs_compare_args(self):
        parser = _build_parser()
        args = parser.parse_args(["runs", "compare", "id-a", "id-b"])
        assert args.runs_action == "compare"
        assert args.run_a == "id-a"
        assert args.run_b == "id-b"

    def test_eval_args(self):
        parser = _build_parser()
        args = parser.parse_args(["eval", "suite.yaml", "-c", "3", "-t", "smoke"])
        assert args.suite == "suite.yaml"
        assert args.concurrency == 3
        assert args.tag == ["smoke"]

    def test_generate_args(self):
        parser = _build_parser()
        args = parser.parse_args(["generate", "-d", "build a pipeline", "-o", "out.yaml"])
        assert args.description == "build a pipeline"
        assert args.output == "out.yaml"

    def test_json_flag_global(self):
        parser = _build_parser()
        args = parser.parse_args(["--json", "runs"])
        assert args.json is True

    def test_hub_install_force_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["hub", "install", "test-slug", "--force"])
        assert args.force is True

    def test_schedule_create_args(self):
        parser = _build_parser()
        args = parser.parse_args([
            "schedule", "create", "my-wf", "0 9 * * *",
            "-i", "key=val",
        ])
        assert args.workflow == "my-wf"
        assert args.cron == "0 9 * * *"


# ---------------------------------------------------------------------------
# _print_run_detail - edge cases
# ---------------------------------------------------------------------------


class TestPrintRunDetail:
    def test_run_with_steps(self, capsys):
        run = {
            "run_id": "test-123",
            "workflow_name": "test-wf",
            "status": "completed",
            "total_cost_usd": 0.0042,
            "started_at": "2026-02-28T10:00:00",
            "completed_at": "2026-02-28T10:01:00",
            "error": None,
            "steps": [
                {
                    "step_id": "s1",
                    "status": "completed",
                    "cost_usd": 0.0021,
                    "duration_seconds": 5.2,
                    "attempt": 1,
                }
            ],
            "outputs": {"result": "ok"},
        }
        _print_run_detail(run)
        out = capsys.readouterr().out
        assert "test-123" in out
        assert "test-wf" in out

    def test_run_with_error(self, capsys):
        run = {
            "run_id": "err-456",
            "workflow_name": "bad-wf",
            "status": "failed",
            "total_cost_usd": 0.0,
            "started_at": None,
            "completed_at": None,
            "error": "Step failed: timeout",
            "steps": None,
            "outputs": None,
        }
        _print_run_detail(run)
        out = capsys.readouterr().out
        assert "timeout" in out

    def test_run_minimal(self, capsys):
        """Run with minimal fields should not crash."""
        run = {"run_id": "min-789", "status": "queued"}
        _print_run_detail(run)
        out = capsys.readouterr().out
        assert "min-789" in out


# ---------------------------------------------------------------------------
# _C.supports_color / _color - terminal detection
# ---------------------------------------------------------------------------


class TestColorSupport:
    def test_no_color_env(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert not _C.supports_color()

    def test_color_function_no_color_mode(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        result = _color("hello", _C.GREEN)
        # Should return plain text, no ANSI codes
        assert result == "hello"


# ---------------------------------------------------------------------------
# _cmd_eval - concurrency validation
# ---------------------------------------------------------------------------


class TestCmdEvalValidation:
    def test_zero_concurrency_exits(self):
        args = _ns(
            suite="test.yaml", concurrency=0,
            tag=None, verbose=False,
        )
        with pytest.raises(SystemExit):
            _cmd_eval(args)

    def test_negative_concurrency_exits(self):
        args = _ns(
            suite="test.yaml", concurrency=-1,
            tag=None, verbose=False,
        )
        with pytest.raises(SystemExit):
            _cmd_eval(args)


# ---------------------------------------------------------------------------
# _cmd_worker - missing arq
# ---------------------------------------------------------------------------


class TestCmdWorker:
    def test_worker_arq_not_found(self):
        args = _ns()
        with patch("subprocess.run", side_effect=FileNotFoundError("arq")):
            with pytest.raises(SystemExit):
                _cmd_worker(args)


# ---------------------------------------------------------------------------
# _cmd_health - various responses
# ---------------------------------------------------------------------------


class TestCmdHealth:
    @patch("sandcastle.__main__._get_client")
    def test_health_ok(self, mock_get_client, capsys):
        mock_client = MagicMock()
        mock_client.health.return_value = {
            "status": "ok", "runtime": True, "database": True
        }
        mock_get_client.return_value = mock_client

        args = _ns(url=None, api_key=None, json=False)
        _cmd_health(args)
        out = capsys.readouterr().out
        assert "ok" in out.lower()

    @patch("sandcastle.__main__._get_client")
    def test_health_unhealthy_exits(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "degraded", "database": False}
        mock_get_client.return_value = mock_client

        args = _ns(url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_health(args)

    @patch("sandcastle.__main__._get_client")
    def test_health_connection_error_exits(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.health.side_effect = Exception("connection refused")
        mock_get_client.return_value = mock_client

        args = _ns(url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_health(args)


# ---------------------------------------------------------------------------
# _cmd_mcp - import error handling
# ---------------------------------------------------------------------------


class TestCmdMcp:
    def test_mcp_import_error(self):
        args = _ns(url=None, api_key=None)
        with patch.dict("sys.modules", {"sandcastle.mcp_server": None}):
            with patch("builtins.__import__", side_effect=ImportError("no mcp")):
                with pytest.raises(SystemExit):
                    _cmd_mcp(args)


# ---------------------------------------------------------------------------
# Edge cases in JSON output mode
# ---------------------------------------------------------------------------


class TestJsonOutputMode:
    @patch("sandcastle.__main__._get_client")
    def test_status_json_output(self, mock_get_client, capsys):
        mock_client = MagicMock()
        mock_client.get_run.return_value = {
            "run_id": "abc-123", "status": "completed",
        }
        mock_get_client.return_value = mock_client

        args = _ns(
            run_id="550e8400-e29b-41d4-a716-446655440000",
            json=True, url=None, api_key=None,
        )
        _cmd_status(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "completed"

    @patch("httpx.get")
    def test_runs_json_output(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"run_id": "r1", "status": "completed"}]
        }
        mock_get.return_value = mock_resp

        args = _ns(
            url="http://localhost:8080", api_key=None,
            limit=10, status=None, json=True,
            runs_action=None,
        )
        _cmd_runs(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "data" in data


# ---------------------------------------------------------------------------
# Integration: _cmd_run with input file and pairs merge
# ---------------------------------------------------------------------------


class TestCmdRunInputMerge:
    @patch("sandcastle.__main__._get_client")
    def test_file_and_pairs_merge(self, mock_get_client, tmp_path):
        """--input pairs should override file values."""
        input_file = tmp_path / "input.json"
        input_file.write_text('{"a": 1, "b": 2}')

        mock_client = MagicMock()
        mock_client.run.return_value = MagicMock(run_id="run-merge")
        mock_get_client.return_value = mock_client

        args = _ns(
            workflow="test-wf",
            input=["b=99", "c=new"],
            input_file=str(input_file),
            wait=False, max_cost=None, json=False,
            url=None, api_key=None,
        )
        _cmd_run(args)

        # Verify the merged input was passed to the client
        call_kwargs = mock_client.run.call_args
        input_sent = call_kwargs[1].get("input") or call_kwargs.kwargs.get("input")
        assert input_sent["a"] == 1
        assert input_sent["b"] == 99  # overridden by --input pair
        assert input_sent["c"] == "new"  # added by --input pair


# ---------------------------------------------------------------------------
# _cmd_generate - output file error handling
# ---------------------------------------------------------------------------


class TestCmdGenerateOutput:
    @patch("sandcastle.engine.generator.generate_workflow_sync")
    def test_generate_write_error_exits(self, mock_gen):
        result_mock = MagicMock()
        result_mock.validation_errors = []
        result_mock.name = "TestWf"
        result_mock.steps_count = 2
        result_mock.yaml_content = "name: test\nsteps: []\n"
        mock_gen.return_value = result_mock

        args = _ns(
            description="test workflow",
            output="/nonexistent/dir/file.yaml",
            refine=False,
        )
        # The mkdir will work for the generate command now, so we need
        # to mock the write to fail
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with patch("pathlib.Path.mkdir"):
                with pytest.raises(SystemExit):
                    _cmd_generate(args)


# ---------------------------------------------------------------------------
# _cmd_init - non-interactive mode
# ---------------------------------------------------------------------------


class TestCmdInit:
    def test_init_non_interactive_exits(self):
        args = _ns()
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(SystemExit):
                from sandcastle.__main__ import _cmd_init
                _cmd_init(args)


# ---------------------------------------------------------------------------
# Hub collection install - error handling
# ---------------------------------------------------------------------------


class TestHubCollectionInstall:
    @patch("sandcastle.__main__._fetch_hub_registry")
    def test_collection_not_found_exits(self, mock_fetch):
        mock_fetch.return_value = {
            "collections": [{"id": "other-col"}],
            "templates": [],
        }
        args = _ns(
            collection_id="nonexistent",
            dir=None, force=False, json=False,
        )
        from sandcastle.__main__ import _cmd_hub_install_collection
        with pytest.raises(SystemExit):
            _cmd_hub_install_collection(args)


# ---------------------------------------------------------------------------
# Hub publish - file not found
# ---------------------------------------------------------------------------


class TestHubPublish:
    def test_publish_file_not_found_exits(self):
        args = _ns(file="/nonexistent/workflow.yaml", json=False)
        from sandcastle.__main__ import _cmd_hub_publish
        with pytest.raises(SystemExit):
            _cmd_hub_publish(args)


# ---------------------------------------------------------------------------
# _cmd_keys_delete - confirmation prompt
# ---------------------------------------------------------------------------


class TestCmdKeysDelete:
    def test_delete_abort_on_no(self, capsys):
        args = _ns(
            key_id="key-123", force=False,
            url=None, api_key=None, json=False,
        )
        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="n"):
            mock_stdin.isatty.return_value = True
            _cmd_keys_delete(args)
            out = capsys.readouterr().out
            assert "Aborted" in out


# ---------------------------------------------------------------------------
# DLQ / violations / tools / autopilot routing
# ---------------------------------------------------------------------------


class TestSubcommandRouting:
    def test_dlq_no_action_exits(self):
        from sandcastle.__main__ import _cmd_dlq
        args = _ns(dlq_action=None, url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_dlq(args)

    def test_violations_no_action_exits(self):
        from sandcastle.__main__ import _cmd_violations
        args = _ns(violations_action=None, url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_violations(args)

    def test_tools_no_action_exits(self):
        from sandcastle.__main__ import _cmd_tools
        args = _ns(tools_action=None, url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_tools(args)

    def test_autopilot_no_action_exits(self):
        from sandcastle.__main__ import _cmd_autopilot
        args = _ns(autopilot_action=None, url=None, api_key=None, json=False)
        with pytest.raises(SystemExit):
            _cmd_autopilot(args)
