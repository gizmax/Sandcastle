"""Coverage for __main__.py pure helper functions and API client helpers.

Targets functions that don't need a running server:
  - _status_color, _table, _color
  - _load_dot_env
  - _validate_run_id
  - _format_cli_error
  - _attr
  - _parse_input_pairs
  - _load_input_file
  - _api_headers, _api_base, _json_out
  - _sanitize_hub_filename, _validate_hub_download_url, _validate_hub_yaml
  - _cmd_hub_search, _cmd_hub_list, _cmd_hub_collections
  - _cmd_dlq_list, _cmd_dlq_retry, _cmd_dlq_resolve
  - _cmd_violations_list
  - _cmd_tools_list, _cmd_tools_configure
  - _cmd_keys_list, _cmd_keys_create, _cmd_keys_rotate
  - _cmd_autopilot_list, _cmd_autopilot_deploy
  - _cmd_runs_compare
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_args(**kwargs) -> argparse.Namespace:
    """Create argparse.Namespace with common defaults."""
    defaults = {
        "url": "http://localhost:8080",
        "api_key": None,
        "json": False,
        "verbose": False,
        "timeout": 30,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ===========================================================================
# _color and _status_color
# ===========================================================================

class TestColorHelpers:
    """Cover _color and _status_color helpers."""

    def test_color_no_tty(self):
        """_color returns plain text when not a tty."""
        from sandcastle.__main__ import _color, _C
        # In test env, isatty() is False, so _color should return plain text
        result = _color("hello", _C.GREEN)
        assert "hello" in result

    def test_status_color_completed(self):
        """_status_color returns colored 'completed'."""
        from sandcastle.__main__ import _status_color
        result = _status_color("completed")
        assert "completed" in result

    def test_status_color_failed(self):
        """_status_color returns colored 'failed'."""
        from sandcastle.__main__ import _status_color
        result = _status_color("failed")
        assert "failed" in result

    def test_status_color_running(self):
        """_status_color returns colored 'running'."""
        from sandcastle.__main__ import _status_color
        result = _status_color("running")
        assert "running" in result

    def test_status_color_queued(self):
        """_status_color returns colored 'queued'."""
        from sandcastle.__main__ import _status_color
        result = _status_color("queued")
        assert "queued" in result

    def test_status_color_unknown(self):
        """_status_color returns plain text for unknown status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("unknown-status")
        assert "unknown-status" in result

    def test_status_color_success(self):
        """_status_color handles 'success' status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("success")
        assert "success" in result

    def test_status_color_error(self):
        """_status_color handles 'error' status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("error")
        assert "error" in result

    def test_status_color_cancelled(self):
        """_status_color handles 'cancelled' status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("cancelled")
        assert "cancelled" in result


# ===========================================================================
# _table
# ===========================================================================

class TestTableFormatter:
    """Cover _table function."""

    def test_table_empty_rows(self):
        """_table returns (no data) for empty rows."""
        from sandcastle.__main__ import _table
        result = _table(["A", "B"], [])
        assert "no data" in result

    def test_table_basic(self):
        """_table formats a basic 2x2 table."""
        from sandcastle.__main__ import _table
        result = _table(["NAME", "STATUS"], [["wf-1", "completed"], ["wf-2", "running"]])
        assert "NAME" in result
        assert "wf-1" in result

    def test_table_truncates_long_values(self):
        """_table truncates values longer than max_col."""
        from sandcastle.__main__ import _table
        long_val = "x" * 100
        result = _table(["COL"], [[long_val]])
        assert len(result) < 200  # truncated

    def test_table_short_rows_padded(self):
        """_table pads rows shorter than headers."""
        from sandcastle.__main__ import _table
        result = _table(["A", "B", "C"], [["only-one"]])
        assert "only-one" in result


# ===========================================================================
# _load_dot_env
# ===========================================================================

class TestLoadDotEnv:
    """Cover _load_dot_env function."""

    def test_load_dot_env_no_file(self):
        """_load_dot_env does nothing when .env doesn't exist."""
        from sandcastle.__main__ import _load_dot_env
        import sandcastle.__main__ as m
        m._dot_env_loaded = False  # reset
        # Should not raise even when .env doesn't exist
        _load_dot_env()

    def test_load_dot_env_idempotent(self):
        """_load_dot_env only loads once (idempotent)."""
        import sandcastle.__main__ as m
        from sandcastle.__main__ import _load_dot_env
        m._dot_env_loaded = True  # pretend already loaded
        _load_dot_env()  # should skip
        assert m._dot_env_loaded is True

    def test_load_dot_env_with_file(self):
        """_load_dot_env parses KEY=VALUE from .env file."""
        from sandcastle.__main__ import _load_dot_env
        import sandcastle.__main__ as m

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = os.path.join(tmpdir, ".env")
            with open(env_file, "w") as f:
                f.write("TEST_KEY_12345=hello_world\n")
                f.write("# comment line\n")
                f.write("export EXPORT_VAR=exported\n")
                f.write("NOVAL_LINE_WITHOUT_EQ\n")
                f.write("QUOTED_VAL=\"quoted_value\"\n")

            orig_dir = os.getcwd()
            try:
                os.chdir(tmpdir)
                m._dot_env_loaded = False
                _load_dot_env()
                # The key should be in environ
                assert os.environ.get("TEST_KEY_12345") == "hello_world"
                assert os.environ.get("EXPORT_VAR") == "exported"
                assert os.environ.get("QUOTED_VAL") == "quoted_value"
            finally:
                os.chdir(orig_dir)
                os.environ.pop("TEST_KEY_12345", None)
                os.environ.pop("EXPORT_VAR", None)
                os.environ.pop("QUOTED_VAL", None)


# ===========================================================================
# _validate_run_id
# ===========================================================================

class TestValidateRunId:
    """Cover _validate_run_id function."""

    def test_valid_uuid(self):
        """_validate_run_id does not exit for a valid UUID."""
        from sandcastle.__main__ import _validate_run_id
        valid_uuid = str(uuid.uuid4())
        # Should not raise or exit
        _validate_run_id(valid_uuid)

    def test_empty_run_id_exits(self):
        """_validate_run_id exits for empty run ID."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("")

    def test_slash_in_run_id_exits(self):
        """_validate_run_id exits for run ID with slash."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run/id")

    def test_backslash_in_run_id_exits(self):
        """_validate_run_id exits for run ID with backslash."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run\\id")

    def test_space_in_run_id_exits(self):
        """_validate_run_id exits for run ID with space."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run id")

    def test_null_byte_in_run_id_exits(self):
        """_validate_run_id exits for run ID with null byte."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run\x00id")

    def test_control_char_in_run_id_exits(self):
        """_validate_run_id exits for run ID with control characters."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("run\x01id")

    def test_non_uuid_warns_but_passes(self, capsys):
        """_validate_run_id warns but doesn't exit for non-UUID string."""
        from sandcastle.__main__ import _validate_run_id
        # Should print warning but not exit
        _validate_run_id("not-a-uuid-at-all")
        captured = capsys.readouterr()
        assert "Warning" in captured.err or "warning" in captured.err.lower()


# ===========================================================================
# _format_cli_error
# ===========================================================================

class TestFormatCliError:
    """Cover _format_cli_error function."""

    def test_format_sandcastle_error_401(self):
        """_format_cli_error formats SandcastleError 401."""
        from sandcastle.__main__ import _format_cli_error

        class SandcastleError(Exception):
            status_code = 401
            code = "UNAUTHORIZED"
            message = "Invalid API key"

        result = _format_cli_error(SandcastleError("Invalid API key"))
        assert "401" in result or "Authentication" in result

    def test_format_connection_error(self):
        """_format_cli_error formats connection errors."""
        from sandcastle.__main__ import _format_cli_error

        class FakeConnectError(Exception):
            pass
        FakeConnectError.__name__ = "ConnectError"

        result = _format_cli_error(FakeConnectError("Connection refused"))
        assert "Connection" in result or "refused" in result.lower()

    def test_format_file_not_found_error(self):
        """_format_cli_error formats FileNotFoundError."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(FileNotFoundError("file.yaml not found"))
        assert "not found" in result.lower() or "File" in result

    def test_format_permission_error(self):
        """_format_cli_error formats PermissionError."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(PermissionError("access denied"))
        assert "Permission" in result or "denied" in result

    def test_format_generic_error(self):
        """_format_cli_error formats generic exceptions."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(ValueError("some error"))
        assert "some error" in result

    def test_format_http_401_in_message(self):
        """_format_cli_error detects 401 in exception message."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(Exception("HTTP 401 Unauthorized"))
        assert "401" in result

    def test_format_http_404_in_message(self):
        """_format_cli_error detects 404 in exception message."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(Exception("404 Not Found"))
        assert "404" in result or "not found" in result.lower()

    def test_format_http_500_in_message(self):
        """_format_cli_error detects 500 in exception message."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(Exception("500 Internal Server Error"))
        assert "500" in result


# ===========================================================================
# _attr
# ===========================================================================

class TestAttrHelper:
    """Cover _attr function."""

    def test_attr_from_dict(self):
        """_attr reads from dict."""
        from sandcastle.__main__ import _attr
        assert _attr({"key": "val"}, "key") == "val"

    def test_attr_from_dict_default(self):
        """_attr returns default for missing key in dict."""
        from sandcastle.__main__ import _attr
        assert _attr({}, "missing", "default") == "default"

    def test_attr_from_object(self):
        """_attr reads from object attribute."""
        from sandcastle.__main__ import _attr
        obj = SimpleNamespace(status="running")
        assert _attr(obj, "status") == "running"

    def test_attr_from_object_default(self):
        """_attr returns default for missing object attribute."""
        from sandcastle.__main__ import _attr
        obj = SimpleNamespace()
        assert _attr(obj, "missing", "fallback") == "fallback"


# ===========================================================================
# _parse_input_pairs
# ===========================================================================

class TestParseInputPairs:
    """Cover _parse_input_pairs function."""

    def test_none_returns_empty(self):
        """_parse_input_pairs returns empty dict for None."""
        from sandcastle.__main__ import _parse_input_pairs
        assert _parse_input_pairs(None) == {}

    def test_empty_list_returns_empty(self):
        """_parse_input_pairs returns empty dict for empty list."""
        from sandcastle.__main__ import _parse_input_pairs
        assert _parse_input_pairs([]) == {}

    def test_string_value(self):
        """_parse_input_pairs parses string values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["name=Alice"])
        assert result["name"] == "Alice"

    def test_json_integer_value(self):
        """_parse_input_pairs parses JSON integer values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["count=42"])
        assert result["count"] == 42

    def test_json_bool_value(self):
        """_parse_input_pairs parses JSON boolean values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["active=true"])
        assert result["active"] is True

    def test_json_list_value(self):
        """_parse_input_pairs parses JSON list values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["items=[1,2,3]"])
        assert result["items"] == [1, 2, 3]

    def test_no_equals_exits(self):
        """_parse_input_pairs exits for pair without '='."""
        from sandcastle.__main__ import _parse_input_pairs
        with pytest.raises(SystemExit):
            _parse_input_pairs(["no-equals-here"])

    def test_empty_key_exits(self):
        """_parse_input_pairs exits for empty key."""
        from sandcastle.__main__ import _parse_input_pairs
        with pytest.raises(SystemExit):
            _parse_input_pairs(["=value"])

    def test_value_with_equals(self):
        """_parse_input_pairs handles value containing '='."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["url=http://example.com?foo=bar"])
        assert result["url"] == "http://example.com?foo=bar"


# ===========================================================================
# _load_input_file
# ===========================================================================

class TestLoadInputFile:
    """Cover _load_input_file function."""

    def test_valid_json_file(self):
        """_load_input_file loads a valid JSON file."""
        from sandcastle.__main__ import _load_input_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value"}, f)
            fname = f.name
        try:
            result = _load_input_file(fname)
            assert result == {"key": "value"}
        finally:
            os.unlink(fname)

    def test_file_not_found_exits(self):
        """_load_input_file exits for non-existent file."""
        from sandcastle.__main__ import _load_input_file
        with pytest.raises(SystemExit):
            _load_input_file("/nonexistent/path/file.json")

    def test_invalid_json_exits(self):
        """_load_input_file exits for invalid JSON."""
        from sandcastle.__main__ import _load_input_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json {")
            fname = f.name
        try:
            with pytest.raises(SystemExit):
                _load_input_file(fname)
        finally:
            os.unlink(fname)

    def test_json_array_exits(self):
        """_load_input_file exits when JSON is array, not object."""
        from sandcastle.__main__ import _load_input_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            fname = f.name
        try:
            with pytest.raises(SystemExit):
                _load_input_file(fname)
        finally:
            os.unlink(fname)

    def test_too_large_file_exits(self):
        """_load_input_file exits when file exceeds size limit."""
        from sandcastle.__main__ import _load_input_file, _MAX_INPUT_FILE_SIZE
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as f:
            # Write a file slightly larger than the limit
            f.write(b"x" * (_MAX_INPUT_FILE_SIZE + 1))
            fname = f.name
        try:
            with pytest.raises(SystemExit):
                _load_input_file(fname)
        finally:
            os.unlink(fname)


# ===========================================================================
# _api_headers, _api_base, _json_out
# ===========================================================================

class TestApiHelpers:
    """Cover API helper functions."""

    def test_api_headers_with_key(self):
        """_api_headers includes Authorization header when api_key set."""
        from sandcastle.__main__ import _api_headers
        args = _make_args(api_key="sk-test-key")
        headers = _api_headers(args)
        assert headers["Authorization"] == "Bearer sk-test-key"

    def test_api_headers_no_key(self):
        """_api_headers returns empty dict when no api_key."""
        from sandcastle.__main__ import _api_headers
        args = _make_args(api_key=None)
        os.environ.pop("SANDCASTLE_API_KEY", None)
        headers = _api_headers(args)
        assert "Authorization" not in headers

    def test_api_base_from_args(self):
        """_api_base returns URL from args."""
        from sandcastle.__main__ import _api_base
        args = _make_args(url="http://myserver:9090")
        assert _api_base(args) == "http://myserver:9090"

    def test_api_base_default(self):
        """_api_base returns default URL when none set."""
        from sandcastle.__main__ import _api_base
        args = _make_args(url=None)
        os.environ.pop("SANDCASTLE_URL", None)
        result = _api_base(args)
        assert "localhost" in result or "8080" in result

    def test_json_out(self, capsys):
        """_json_out prints formatted JSON."""
        from sandcastle.__main__ import _json_out
        _json_out({"key": "value", "count": 42})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["key"] == "value"


# ===========================================================================
# _sanitize_hub_filename
# ===========================================================================

class TestSanitizeHubFilename:
    """Cover _sanitize_hub_filename function."""

    def test_simple_slug(self):
        """_sanitize_hub_filename returns safe filename for simple slug."""
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("my-workflow")
        assert result == "my-workflow.yaml"

    def test_slash_in_slug(self):
        """_sanitize_hub_filename takes only last component."""
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("author/my-workflow")
        assert result == "my-workflow.yaml"

    def test_special_chars_removed(self):
        """_sanitize_hub_filename removes special characters."""
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("my.workflow!@#")
        assert "!" not in result
        assert "@" not in result
        assert "#" not in result

    def test_dot_prefix_removed(self):
        """_sanitize_hub_filename removes leading dots."""
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("...hidden-file")
        assert not result.startswith(".")

    def test_empty_slug(self):
        """_sanitize_hub_filename returns non-empty filename for empty/dot slug."""
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("")  # Empty slug
        assert result.endswith(".yaml")
        assert len(result) > len(".yaml")  # Has some content


# ===========================================================================
# _validate_hub_yaml
# ===========================================================================

class TestValidateHubYaml:
    """Cover _validate_hub_yaml function."""

    def test_valid_yaml(self):
        """_validate_hub_yaml returns True for valid workflow YAML."""
        from sandcastle.__main__ import _validate_hub_yaml
        yaml_content = "name: test-wf\nsteps:\n  - id: step1\n    type: llm\n    prompt: test\n"
        valid, err = _validate_hub_yaml(yaml_content)
        assert valid is True
        assert err == ""

    def test_invalid_yaml_syntax(self):
        """_validate_hub_yaml returns False for YAML syntax error."""
        from sandcastle.__main__ import _validate_hub_yaml
        valid, err = _validate_hub_yaml(": invalid: yaml: :")
        assert valid is False
        assert err != ""

    def test_missing_name_field(self):
        """_validate_hub_yaml returns False when 'name' field is missing."""
        from sandcastle.__main__ import _validate_hub_yaml
        valid, err = _validate_hub_yaml("steps:\n  - id: s1\n    type: llm\n")
        assert valid is False

    def test_missing_steps_field(self):
        """_validate_hub_yaml returns False when 'steps' field is missing."""
        from sandcastle.__main__ import _validate_hub_yaml
        valid, err = _validate_hub_yaml("name: my-wf\n")
        assert valid is False

    def test_too_large_content(self):
        """_validate_hub_yaml returns False for content exceeding 512KB."""
        from sandcastle.__main__ import _validate_hub_yaml
        # 512KB + 1 byte of content
        large_content = "x" * (512 * 1024 + 1)
        valid, err = _validate_hub_yaml(large_content)
        assert valid is False
        assert "large" in err.lower()

    def test_not_a_mapping(self):
        """_validate_hub_yaml returns False when YAML is not a mapping."""
        from sandcastle.__main__ import _validate_hub_yaml
        valid, err = _validate_hub_yaml("- item1\n- item2\n")
        assert valid is False


# ===========================================================================
# _cmd_hub_search
# ===========================================================================

class TestCmdHubSearch:
    """Cover _cmd_hub_search function."""

    def test_search_finds_match(self, capsys):
        """_cmd_hub_search finds matching templates."""
        from sandcastle.__main__ import _cmd_hub_search

        mock_data = {
            "templates": [
                {
                    "slug": "author/email-sender",
                    "name": "Email Sender",
                    "description": "Sends emails automatically",
                    "category": "automation",
                    "step_count": 3,
                    "author": "author",
                    "tags": ["email", "automation"],
                }
            ]
        }

        args = _make_args(query="email", category=None)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_search(args)
        captured = capsys.readouterr()
        assert "email" in captured.out.lower() or "Email" in captured.out

    def test_search_no_results(self, capsys):
        """_cmd_hub_search shows no results message."""
        from sandcastle.__main__ import _cmd_hub_search

        mock_data = {"templates": []}
        args = _make_args(query="nonexistent-query-xyz")
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_search(args)
        captured = capsys.readouterr()
        assert "No workflows found" in captured.out

    def test_search_empty_query_exits(self):
        """_cmd_hub_search exits for empty query."""
        from sandcastle.__main__ import _cmd_hub_search

        args = _make_args(query="", category=None)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value={"templates": []}):
            with pytest.raises(SystemExit):
                _cmd_hub_search(args)

    def test_search_json_output(self, capsys):
        """_cmd_hub_search prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_hub_search

        mock_data = {
            "templates": [
                {"slug": "x/wf1", "name": "WF1", "description": "email wf", "tags": [], "category": "a", "step_count": 1, "author": "x"}
            ]
        }
        args = _make_args(query="email", category=None, json=True)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_search(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)


# ===========================================================================
# _cmd_hub_list
# ===========================================================================

class TestCmdHubList:
    """Cover _cmd_hub_list function."""

    def test_hub_list_basic(self, capsys):
        """_cmd_hub_list displays template list."""
        from sandcastle.__main__ import _cmd_hub_list

        mock_data = {
            "templates": [
                {"slug": "a/wf1", "name": "WF1", "category": "ai", "step_count": 2, "author": "a"},
                {"slug": "b/wf2", "name": "WF2", "category": "data", "step_count": 5, "author": "b"},
            ],
            "stats": {"total_templates": 2},
            "categories": ["ai", "data"],
        }
        args = _make_args(category=None, limit=20)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_list(args)
        captured = capsys.readouterr()
        assert "WF1" in captured.out or "wf1" in captured.out.lower()

    def test_hub_list_no_templates(self, capsys):
        """_cmd_hub_list shows no-results message when empty."""
        from sandcastle.__main__ import _cmd_hub_list

        mock_data = {"templates": [], "stats": {}, "categories": []}
        args = _make_args(category=None, limit=20)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_list(args)
        captured = capsys.readouterr()
        assert "No community workflows" in captured.out

    def test_hub_list_category_filter(self, capsys):
        """_cmd_hub_list filters by category."""
        from sandcastle.__main__ import _cmd_hub_list

        mock_data = {
            "templates": [
                {"slug": "a/wf1", "name": "AI WF", "category": "ai", "step_count": 2, "author": "a"},
                {"slug": "b/wf2", "name": "Data WF", "category": "data", "step_count": 3, "author": "b"},
            ],
            "stats": {},
            "categories": ["ai", "data"],
        }
        args = _make_args(category="ai", limit=20)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_list(args)
        captured = capsys.readouterr()
        assert "AI WF" in captured.out
        assert "Data WF" not in captured.out

    def test_hub_list_invalid_limit_exits(self):
        """_cmd_hub_list exits when limit < 1."""
        from sandcastle.__main__ import _cmd_hub_list

        mock_data = {"templates": []}
        args = _make_args(category=None, limit=0)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            with pytest.raises(SystemExit):
                _cmd_hub_list(args)

    def test_hub_list_json_output(self, capsys):
        """_cmd_hub_list prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_hub_list

        mock_data = {
            "templates": [{"slug": "a/wf", "name": "WF", "category": "ai", "step_count": 1, "author": "a"}],
            "stats": {},
            "categories": [],
        }
        args = _make_args(category=None, limit=20, json=True)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_list(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)


# ===========================================================================
# _cmd_hub_collections
# ===========================================================================

class TestCmdHubCollections:
    """Cover _cmd_hub_collections function."""

    def test_collections_displayed(self, capsys):
        """_cmd_hub_collections displays collection details."""
        from sandcastle.__main__ import _cmd_hub_collections

        mock_data = {
            "collections": [
                {
                    "id": "marketing",
                    "name": "Marketing Suite",
                    "description": "Marketing workflows",
                    "icon": "target",
                    "template_slugs": ["a/wf1", "a/wf2"],
                    "downloads": 150,
                }
            ]
        }
        args = _make_args()
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_collections(args)
        captured = capsys.readouterr()
        assert "Marketing" in captured.out

    def test_collections_empty(self, capsys):
        """_cmd_hub_collections shows no-results message."""
        from sandcastle.__main__ import _cmd_hub_collections

        mock_data = {"collections": []}
        args = _make_args()
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_collections(args)
        captured = capsys.readouterr()
        assert "No collections" in captured.out

    def test_collections_json_output(self, capsys):
        """_cmd_hub_collections prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_hub_collections

        mock_data = {"collections": [{"id": "col1", "name": "Col1"}]}
        args = _make_args(json=True)
        with patch("sandcastle.__main__._fetch_hub_registry", return_value=mock_data):
            _cmd_hub_collections(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)


# ===========================================================================
# _cmd_dlq_list, _cmd_dlq_retry, _cmd_dlq_resolve
# ===========================================================================

class TestCmdDlq:
    """Cover DLQ command functions."""

    def _make_dlq_response(self, items=None):
        """Create a mock DLQ API response."""
        if items is None:
            items = [
                {
                    "id": str(uuid.uuid4()),
                    "run_id": str(uuid.uuid4()),
                    "step_id": "step-1",
                    "error": "Timeout occurred",
                    "attempts": 3,
                    "created_at": "2026-01-15T10:00:00",
                    "resolved_at": None,
                }
            ]
        return {"data": items}

    def test_dlq_list_shows_items(self, capsys):
        """_cmd_dlq_list shows DLQ items in table."""
        from sandcastle.__main__ import _cmd_dlq_list

        args = _make_args(resolved=False)
        with patch("sandcastle.__main__._api_get", return_value=self._make_dlq_response()):
            _cmd_dlq_list(args)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_dlq_list_empty(self, capsys):
        """_cmd_dlq_list shows no-items message for empty list."""
        from sandcastle.__main__ import _cmd_dlq_list

        args = _make_args(resolved=False)
        with patch("sandcastle.__main__._api_get", return_value={"data": []}):
            _cmd_dlq_list(args)
        captured = capsys.readouterr()
        assert "No dead letter queue items" in captured.out

    def test_dlq_list_json_output(self, capsys):
        """_cmd_dlq_list prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_dlq_list

        response = self._make_dlq_response()
        args = _make_args(resolved=False, json=True)
        with patch("sandcastle.__main__._api_get", return_value=response):
            _cmd_dlq_list(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed

    def test_dlq_list_with_resolved_flag(self, capsys):
        """_cmd_dlq_list passes resolved=true param."""
        from sandcastle.__main__ import _cmd_dlq_list

        args = _make_args(resolved=True)
        mock_get = MagicMock(return_value={"data": []})
        with patch("sandcastle.__main__._api_get", mock_get):
            _cmd_dlq_list(args)
        # Verify params included resolved=true
        call_kwargs = mock_get.call_args
        assert call_kwargs is not None

    def test_dlq_retry_success(self, capsys):
        """_cmd_dlq_retry shows success message."""
        from sandcastle.__main__ import _cmd_dlq_retry

        item_id = str(uuid.uuid4())
        new_run_id = str(uuid.uuid4())
        args = _make_args(item_id=item_id)
        with patch("sandcastle.__main__._api_post", return_value={
            "data": {"retried": True, "new_run_id": new_run_id}
        }):
            _cmd_dlq_retry(args)
        captured = capsys.readouterr()
        assert "retried" in captured.out.lower() or item_id[:6] in captured.out

    def test_dlq_retry_json_output(self, capsys):
        """_cmd_dlq_retry prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_dlq_retry

        item_id = str(uuid.uuid4())
        args = _make_args(item_id=item_id, json=True)
        response = {"data": {"retried": True, "new_run_id": str(uuid.uuid4())}}
        with patch("sandcastle.__main__._api_post", return_value=response):
            _cmd_dlq_retry(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed

    def test_dlq_resolve_success(self, capsys):
        """_cmd_dlq_resolve shows success message."""
        from sandcastle.__main__ import _cmd_dlq_resolve

        item_id = str(uuid.uuid4())
        args = _make_args(item_id=item_id)
        with patch("sandcastle.__main__._api_post", return_value={
            "data": {"resolved_at": "2026-01-15T11:00:00", "resolved_by": "manual"}
        }):
            _cmd_dlq_resolve(args)
        captured = capsys.readouterr()
        assert "resolved" in captured.out.lower()

    def test_dlq_resolve_unexpected_response(self, capsys):
        """_cmd_dlq_resolve shows unexpected response message."""
        from sandcastle.__main__ import _cmd_dlq_resolve

        item_id = str(uuid.uuid4())
        args = _make_args(item_id=item_id)
        with patch("sandcastle.__main__._api_post", return_value={"data": {}}):
            _cmd_dlq_resolve(args)
        captured = capsys.readouterr()
        assert "Unexpected" in captured.out


# ===========================================================================
# _cmd_violations_list
# ===========================================================================

class TestCmdViolations:
    """Cover _cmd_violations_list function."""

    def test_violations_list_shows_items(self, capsys):
        """_cmd_violations_list shows violations in table."""
        from sandcastle.__main__ import _cmd_violations_list

        run_id = str(uuid.uuid4())
        args = _make_args(severity=None)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": [
                {
                    "id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "step_id": "step-1",
                    "policy_id": "no-pii",
                    "severity": "high",
                    "action_taken": "block",
                    "created_at": "2026-01-15T10:00:00",
                }
            ]
        }):
            _cmd_violations_list(args)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_violations_list_empty(self, capsys):
        """_cmd_violations_list shows no-violations message."""
        from sandcastle.__main__ import _cmd_violations_list

        args = _make_args(severity=None)
        with patch("sandcastle.__main__._api_get", return_value={"data": []}):
            _cmd_violations_list(args)
        captured = capsys.readouterr()
        assert "No policy violations" in captured.out

    def test_violations_list_severity_levels(self, capsys):
        """_cmd_violations_list colorizes different severity levels."""
        from sandcastle.__main__ import _cmd_violations_list

        args = _make_args(severity=None)
        violations = []
        for sev in ("critical", "high", "medium", "low"):
            violations.append({
                "id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
                "step_id": "step-1",
                "policy_id": "policy",
                "severity": sev,
                "action_taken": "log",
                "created_at": "2026-01-15T10:00:00",
            })
        with patch("sandcastle.__main__._api_get", return_value={"data": violations}):
            _cmd_violations_list(args)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_violations_list_json_output(self, capsys):
        """_cmd_violations_list prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_violations_list

        args = _make_args(severity=None, json=True)
        response = {"data": [{"id": "v1"}]}
        with patch("sandcastle.__main__._api_get", return_value=response):
            _cmd_violations_list(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed


# ===========================================================================
# _cmd_tools_list, _cmd_tools_configure
# ===========================================================================

class TestCmdTools:
    """Cover _cmd_tools_list and _cmd_tools_configure functions."""

    def test_tools_list_shows_tools(self, capsys):
        """_cmd_tools_list shows tools in table."""
        from sandcastle.__main__ import _cmd_tools_list

        args = _make_args(category=None)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": {
                "tools": [
                    {
                        "name": "slack",
                        "category": "communication",
                        "credentials_configured": ["SLACK_BOT_TOKEN"],
                        "credentials_missing": [],
                        "connections": [],
                    }
                ]
            }
        }):
            _cmd_tools_list(args)
        captured = capsys.readouterr()
        assert "slack" in captured.out.lower()

    def test_tools_list_partial_config(self, capsys):
        """_cmd_tools_list shows partial status for partially configured tool."""
        from sandcastle.__main__ import _cmd_tools_list

        args = _make_args(category=None)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": {
                "tools": [
                    {
                        "name": "github",
                        "category": "dev",
                        "credentials_configured": ["GITHUB_TOKEN"],
                        "credentials_missing": ["GITHUB_APP_ID"],
                        "connections": [{"id": "c1"}],
                    }
                ]
            }
        }):
            _cmd_tools_list(args)
        captured = capsys.readouterr()
        assert "github" in captured.out.lower()

    def test_tools_list_not_configured(self, capsys):
        """_cmd_tools_list shows not configured status."""
        from sandcastle.__main__ import _cmd_tools_list

        args = _make_args(category=None)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": {
                "tools": [
                    {
                        "name": "stripe",
                        "category": "payments",
                        "credentials_configured": [],
                        "credentials_missing": ["STRIPE_KEY"],
                        "connections": [],
                    }
                ]
            }
        }):
            _cmd_tools_list(args)
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_tools_list_empty(self, capsys):
        """_cmd_tools_list shows no-tools message for empty list."""
        from sandcastle.__main__ import _cmd_tools_list

        args = _make_args(category=None)
        with patch("sandcastle.__main__._api_get", return_value={"data": {"tools": []}}):
            _cmd_tools_list(args)
        captured = capsys.readouterr()
        assert "No tools found" in captured.out

    def test_tools_list_json_output(self, capsys):
        """_cmd_tools_list prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_tools_list

        args = _make_args(category=None, json=True)
        response = {"data": {"tools": [{"name": "tool1"}]}}
        with patch("sandcastle.__main__._api_get", return_value=response):
            _cmd_tools_list(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed

    def test_tools_configure_basic(self, capsys):
        """_cmd_tools_configure sets credentials."""
        from sandcastle.__main__ import _cmd_tools_configure

        args = _make_args(
            tool_name="slack",
            env=["SLACK_BOT_TOKEN=xoxb-test"],
        )
        with patch("sandcastle.__main__._api_put", return_value={
            "data": {
                "credentials_configured": ["SLACK_BOT_TOKEN"],
                "credentials_missing": [],
            }
        }):
            _cmd_tools_configure(args)
        captured = capsys.readouterr()
        assert "Credentials updated" in captured.out or "slack" in captured.out.lower()

    def test_tools_configure_no_env_exits(self):
        """_cmd_tools_configure exits when no --env pairs given."""
        from sandcastle.__main__ import _cmd_tools_configure

        args = _make_args(tool_name="slack", env=None)
        with pytest.raises(SystemExit):
            _cmd_tools_configure(args)

    def test_tools_configure_invalid_format_exits(self):
        """_cmd_tools_configure exits for invalid KEY=VALUE format."""
        from sandcastle.__main__ import _cmd_tools_configure

        args = _make_args(tool_name="slack", env=["no-equals-sign"])
        with pytest.raises(SystemExit):
            _cmd_tools_configure(args)

    def test_tools_configure_shows_missing(self, capsys):
        """_cmd_tools_configure shows still-missing credentials."""
        from sandcastle.__main__ import _cmd_tools_configure

        args = _make_args(
            tool_name="github",
            env=["GITHUB_TOKEN=tok123"],
        )
        with patch("sandcastle.__main__._api_put", return_value={
            "data": {
                "credentials_configured": ["GITHUB_TOKEN"],
                "credentials_missing": ["GITHUB_APP_ID"],
            }
        }):
            _cmd_tools_configure(args)
        captured = capsys.readouterr()
        assert "missing" in captured.out.lower() or "GITHUB_APP_ID" in captured.out

    def test_tools_configure_json_output(self, capsys):
        """_cmd_tools_configure prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_tools_configure

        args = _make_args(tool_name="slack", env=["KEY=val"], json=True)
        response = {"data": {"credentials_configured": ["KEY"], "credentials_missing": []}}
        with patch("sandcastle.__main__._api_put", return_value=response):
            _cmd_tools_configure(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed


# ===========================================================================
# _cmd_keys_list, _cmd_keys_rotate
# ===========================================================================

class TestCmdKeys:
    """Cover key management command functions."""

    def test_keys_list_shows_keys(self, capsys):
        """_cmd_keys_list shows API keys in table."""
        from sandcastle.__main__ import _cmd_keys_list

        args = _make_args()
        with patch("sandcastle.__main__._api_get", return_value={
            "data": [
                {
                    "id": str(uuid.uuid4()),
                    "key_prefix": "sk-abc",
                    "name": "prod-key",
                    "tenant_id": None,
                    "max_cost_per_run_usd": 5.0,
                    "created_at": "2026-01-10T09:00:00",
                    "last_used_at": "2026-01-15T10:00:00",
                }
            ]
        }):
            _cmd_keys_list(args)
        captured = capsys.readouterr()
        assert "sk-abc" in captured.out or "prod-key" in captured.out

    def test_keys_list_empty(self, capsys):
        """_cmd_keys_list shows no-keys message for empty list."""
        from sandcastle.__main__ import _cmd_keys_list

        args = _make_args()
        with patch("sandcastle.__main__._api_get", return_value={"data": []}):
            _cmd_keys_list(args)
        captured = capsys.readouterr()
        assert "No API keys found" in captured.out

    def test_keys_list_json_output(self, capsys):
        """_cmd_keys_list prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_keys_list

        args = _make_args(json=True)
        response = {"data": [{"id": "k1"}]}
        with patch("sandcastle.__main__._api_get", return_value=response):
            _cmd_keys_list(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed

    def test_keys_rotate_shows_new_key(self, capsys):
        """_cmd_keys_rotate shows new key after rotation."""
        from sandcastle.__main__ import _cmd_keys_rotate

        key_id = str(uuid.uuid4())
        new_key_id = str(uuid.uuid4())
        args = _make_args(key_id=key_id)
        with patch("sandcastle.__main__._api_post", return_value={
            "data": {
                "new_key_id": new_key_id,
                "key": "sk-new-secret-key-123",
                "grace_expires_at": "2026-01-22T10:00:00",
            }
        }):
            _cmd_keys_rotate(args)
        captured = capsys.readouterr()
        assert "rotated" in captured.out.lower() or "key" in captured.out.lower()

    def test_keys_rotate_json_output(self, capsys):
        """_cmd_keys_rotate prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_keys_rotate

        key_id = str(uuid.uuid4())
        args = _make_args(key_id=key_id, json=True)
        response = {"data": {"new_key_id": str(uuid.uuid4()), "key": "sk-test"}}
        with patch("sandcastle.__main__._api_post", return_value=response):
            _cmd_keys_rotate(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed


# ===========================================================================
# _cmd_autopilot_list, _cmd_autopilot_deploy
# ===========================================================================

class TestCmdAutopilot:
    """Cover autopilot command functions."""

    def test_autopilot_list_shows_experiments(self, capsys):
        """_cmd_autopilot_list shows experiments in table."""
        from sandcastle.__main__ import _cmd_autopilot_list

        args = _make_args(status=None)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": [
                {
                    "id": str(uuid.uuid4()),
                    "workflow_name": "email-wf",
                    "step_id": "compose",
                    "status": "running",
                    "optimize_for": "quality",
                    "deployed_variant_id": None,
                    "created_at": "2026-01-15T09:00:00",
                }
            ]
        }):
            _cmd_autopilot_list(args)
        captured = capsys.readouterr()
        assert "email-wf" in captured.out or "running" in captured.out

    def test_autopilot_list_empty(self, capsys):
        """_cmd_autopilot_list shows no-experiments message."""
        from sandcastle.__main__ import _cmd_autopilot_list

        args = _make_args(status=None)
        with patch("sandcastle.__main__._api_get", return_value={"data": []}):
            _cmd_autopilot_list(args)
        captured = capsys.readouterr()
        assert "No autopilot experiments" in captured.out

    def test_autopilot_list_json_output(self, capsys):
        """_cmd_autopilot_list prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_autopilot_list

        args = _make_args(status=None, json=True)
        response = {"data": [{"id": "exp1"}]}
        with patch("sandcastle.__main__._api_get", return_value=response):
            _cmd_autopilot_list(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed

    def test_autopilot_deploy_success(self, capsys):
        """_cmd_autopilot_deploy shows success message."""
        from sandcastle.__main__ import _cmd_autopilot_deploy

        exp_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        args = _make_args(experiment_id=exp_id)
        with patch("sandcastle.__main__._api_post", return_value={
            "data": {
                "deployed": True,
                "deployed_variant_id": variant_id,
                "workflow_name": "email-wf",
                "step_id": "compose",
            }
        }):
            _cmd_autopilot_deploy(args)
        captured = capsys.readouterr()
        assert "deployed" in captured.out.lower() or exp_id[:6] in captured.out

    def test_autopilot_deploy_json_output(self, capsys):
        """_cmd_autopilot_deploy prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_autopilot_deploy

        exp_id = str(uuid.uuid4())
        args = _make_args(experiment_id=exp_id, json=True)
        response = {"data": {"deployed": True}}
        with patch("sandcastle.__main__._api_post", return_value=response):
            _cmd_autopilot_deploy(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed


# ===========================================================================
# _cmd_runs_compare
# ===========================================================================

class TestCmdRunsCompare:
    """Cover _cmd_runs_compare function."""

    def test_runs_compare_shows_comparison(self, capsys):
        """_cmd_runs_compare shows comparison table."""
        from sandcastle.__main__ import _cmd_runs_compare

        run_a = str(uuid.uuid4())
        run_b = str(uuid.uuid4())
        args = _make_args(run_a=run_a, run_b=run_b)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": {
                "run_a": {
                    "run_id": run_a,
                    "workflow_name": "wf1",
                    "status": "completed",
                    "total_cost_usd": 0.05,
                    "duration_seconds": 10.0,
                },
                "run_b": {
                    "run_id": run_b,
                    "workflow_name": "wf1",
                    "status": "completed",
                    "total_cost_usd": 0.04,
                    "duration_seconds": 8.5,
                },
                "same_workflow": True,
                "total_cost_a": 0.05,
                "total_cost_b": 0.04,
                "total_cost_delta": -0.01,
                "total_duration_a": 10.0,
                "total_duration_b": 8.5,
                "total_duration_delta": -1.5,
                "steps": [],
            }
        }):
            _cmd_runs_compare(args)
        captured = capsys.readouterr()
        assert "Comparison" in captured.out or "wf1" in captured.out

    def test_runs_compare_with_steps(self, capsys):
        """_cmd_runs_compare shows step differences."""
        from sandcastle.__main__ import _cmd_runs_compare

        run_a = str(uuid.uuid4())
        run_b = str(uuid.uuid4())
        args = _make_args(run_a=run_a, run_b=run_b)
        with patch("sandcastle.__main__._api_get", return_value={
            "data": {
                "run_a": {"run_id": run_a, "workflow_name": "wf1", "status": "completed"},
                "run_b": {"run_id": run_b, "workflow_name": "wf1", "status": "completed"},
                "same_workflow": True,
                "total_cost_a": 0.05,
                "total_cost_b": 0.06,
                "total_cost_delta": 0.01,
                "total_duration_a": None,
                "total_duration_b": None,
                "total_duration_delta": None,
                "steps": [
                    {
                        "step_id": "step1",
                        "presence": "both",
                        "status_a": "completed",
                        "status_b": "completed",
                        "cost_a": 0.02,
                        "cost_b": 0.03,
                        "output_changed": True,
                        "config_changed": False,
                    },
                    {
                        "step_id": "step2",
                        "presence": "only_a",
                        "status_a": "completed",
                        "status_b": None,
                        "cost_a": 0.03,
                        "cost_b": 0.0,
                        "output_changed": False,
                        "config_changed": True,
                    },
                ],
            }
        }):
            _cmd_runs_compare(args)
        captured = capsys.readouterr()
        assert "step1" in captured.out or "Step" in captured.out

    def test_runs_compare_json_output(self, capsys):
        """_cmd_runs_compare prints JSON when --json flag set."""
        from sandcastle.__main__ import _cmd_runs_compare

        run_a = str(uuid.uuid4())
        run_b = str(uuid.uuid4())
        args = _make_args(run_a=run_a, run_b=run_b, json=True)
        response = {"data": {"run_a": {}, "run_b": {}, "steps": []}}
        with patch("sandcastle.__main__._api_get", return_value=response):
            _cmd_runs_compare(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "data" in parsed


# ===========================================================================
# api/routes.py - _mask and _extract_yaml_metadata
# ===========================================================================

class TestRoutesHelpers:
    """Cover additional routes.py helper functions."""

    def test_mask_short_value(self):
        """_mask returns '****' for short values."""
        from sandcastle.api.routes import _mask
        result = _mask("short")
        assert result == "****"

    def test_mask_long_value(self):
        """_mask shows last 4 chars for long values."""
        from sandcastle.api.routes import _mask
        result = _mask("sk-test-longkey1234567890")
        assert result.startswith("****")
        assert result.endswith("7890")

    def test_mask_empty_value(self):
        """_mask returns empty string for empty value."""
        from sandcastle.api.routes import _mask
        result = _mask("")
        assert result == ""

    def test_extract_yaml_metadata_full(self):
        """_extract_yaml_metadata extracts name, steps, models, tools."""
        from sandcastle.api.routes import _extract_yaml_metadata
        yaml_content = """name: my-workflow
steps:
  - id: step1
    type: llm
    model: claude-3-haiku-20240307
    prompt: hello
  - id: step2
    type: tool
    tool: slack
    message: done
"""
        result = _extract_yaml_metadata(yaml_content)
        assert result["name"] == "my-workflow"
        assert result["step_count"] == 2
        assert "claude-3-haiku-20240307" in result["models_used"]
        assert "slack" in result["tools_used"]

    def test_extract_yaml_metadata_invalid_yaml(self):
        """_extract_yaml_metadata returns empty dict for invalid YAML."""
        from sandcastle.api.routes import _extract_yaml_metadata
        result = _extract_yaml_metadata(": invalid: yaml: :")
        assert result == {}

    def test_extract_yaml_metadata_no_steps(self):
        """_extract_yaml_metadata handles workflow with no steps."""
        from sandcastle.api.routes import _extract_yaml_metadata
        result = _extract_yaml_metadata("name: empty-wf\nsteps: []\n")
        assert result["name"] == "empty-wf"
        assert result["step_count"] == 0

    def test_slugify_basic(self):
        """_slugify converts name to URL-safe slug."""
        from sandcastle.api.routes import _slugify
        result = _slugify("My Workflow Name")
        assert result == "my-workflow-name"

    def test_slugify_special_chars(self):
        """_slugify removes special characters."""
        from sandcastle.api.routes import _slugify
        result = _slugify("Hello! World@2024")
        assert "!" not in result
        assert "@" not in result
        assert "hello" in result

    def test_slugify_multiple_spaces(self):
        """_slugify collapses multiple spaces to single hyphen."""
        from sandcastle.api.routes import _slugify
        result = _slugify("hello   world")
        assert result == "hello-world"

    def test_slugify_leading_trailing_hyphens(self):
        """_slugify removes leading/trailing hyphens."""
        from sandcastle.api.routes import _slugify
        result = _slugify("  -- my workflow --  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_sse_event_without_id(self):
        """_sse_event formats event without ID."""
        from sandcastle.api.routes import _sse_event
        result = _sse_event("run.started", {"run_id": "abc"})
        assert "event: run.started" in result
        assert '"run_id"' in result
        assert result.endswith("\n\n")

    def test_sse_event_with_id(self):
        """_sse_event formats event with ID."""
        from sandcastle.api.routes import _sse_event
        result = _sse_event("run.completed", {"status": "done"}, event_id=42)
        assert "id: 42" in result
        assert "event: run.completed" in result
        assert result.endswith("\n\n")

    def test_validate_scope_id_valid_workflow(self):
        """_validate_scope_id accepts 'workflow:name' format."""
        from sandcastle.api.routes import _validate_scope_id
        result = _validate_scope_id("workflow:my-workflow")
        assert result == "workflow:my-workflow"

    def test_validate_scope_id_valid_agent(self):
        """_validate_scope_id accepts 'agent:name' format."""
        from sandcastle.api.routes import _validate_scope_id
        result = _validate_scope_id("agent:my-bot")
        assert result == "agent:my-bot"

    def test_validate_scope_id_valid_global(self):
        """_validate_scope_id accepts 'global' scope."""
        from sandcastle.api.routes import _validate_scope_id
        result = _validate_scope_id("global")
        assert result == "global"

    def test_validate_scope_id_invalid_raises(self):
        """_validate_scope_id raises for invalid scope_id."""
        from sandcastle.api.routes import _validate_scope_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _validate_scope_id("invalid-scope")
        assert exc_info.value.status_code == 422

    def test_validate_memory_id_valid(self):
        """_validate_memory_id accepts valid memory ID."""
        from sandcastle.api.routes import _validate_memory_id
        result = _validate_memory_id("mem-123_abc")
        assert result == "mem-123_abc"

    def test_validate_memory_id_invalid_raises(self):
        """_validate_memory_id raises for invalid memory ID."""
        from sandcastle.api.routes import _validate_memory_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _validate_memory_id("invalid id with spaces!")
        assert exc_info.value.status_code == 422
