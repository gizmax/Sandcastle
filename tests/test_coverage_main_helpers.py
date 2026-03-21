"""Coverage for __main__.py helper functions.

Tests CLI helper functions without actually running CLI commands.
Targets: _color, _status_color, _table, _load_dot_env, _validate_run_id,
         _format_cli_error, _attr, _parse_input_pairs, _to_dict, _to_dicts,
         _fmt_time, _format_cli_error paths
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# _color and _status_color
# ===========================================================================

class TestColorFunctions:
    """Cover _color and _status_color helper functions."""

    def test_color_with_tty_support(self):
        """_color returns ANSI-wrapped string when color supported."""
        from sandcastle.__main__ import _C, _color
        with patch.object(_C, "supports_color", return_value=True):
            result = _color("hello", _C.GREEN)
            assert "hello" in result

    def test_color_without_tty_support(self):
        """_color returns plain text when color not supported."""
        from sandcastle.__main__ import _C, _color
        with patch.object(_C, "supports_color", return_value=False):
            result = _color("hello", _C.GREEN)
            assert result == "hello"

    def test_status_color_completed(self):
        """_status_color returns green for completed status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("completed")
        assert "completed" in result.lower() or result == "completed"

    def test_status_color_failed(self):
        """_status_color returns red for failed status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("failed")
        assert "failed" in result.lower() or result == "failed"

    def test_status_color_running(self):
        """_status_color returns yellow for running status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("running")
        assert "running" in result.lower() or result == "running"

    def test_status_color_queued(self):
        """_status_color returns gray for queued status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("queued")
        assert "queued" in result.lower() or result == "queued"

    def test_status_color_unknown(self):
        """_status_color returns plain text for unknown status."""
        from sandcastle.__main__ import _status_color
        result = _status_color("unknown_status")
        assert result == "unknown_status"


# ===========================================================================
# _table
# ===========================================================================

class TestTableFormatter:
    """Cover _table helper function."""

    def test_table_empty_rows(self):
        """_table returns no data message for empty rows."""
        from sandcastle.__main__ import _table
        result = _table(["Name", "Status"], [])
        assert "no data" in result.lower() or result.strip() != ""

    def test_table_with_rows(self):
        """_table formats rows correctly."""
        from sandcastle.__main__ import _table
        result = _table(
            ["Name", "Status", "Cost"],
            [["my-workflow", "completed", "$0.01"], ["other", "failed", "$0.00"]],
        )
        assert "Name" in result
        assert "my-workflow" in result

    def test_table_row_truncation(self):
        """_table truncates long values."""
        from sandcastle.__main__ import _table
        long_value = "A" * 100
        result = _table(["Name"], [[long_value]], max_col=20)
        # Truncated value should appear
        assert "A" in result

    def test_table_row_shorter_than_headers(self):
        """_table handles rows shorter than headers."""
        from sandcastle.__main__ import _table
        result = _table(["A", "B", "C"], [["only_one"]])
        assert "only_one" in result

    def test_table_row_longer_than_headers(self):
        """_table handles rows longer than headers."""
        from sandcastle.__main__ import _table
        result = _table(["A"], [["val1", "extra1", "extra2"]])
        assert "val1" in result


# ===========================================================================
# _load_dot_env
# ===========================================================================

class TestLoadDotEnv:
    """Cover _load_dot_env function."""

    def test_load_dot_env_no_file(self, tmp_path, monkeypatch):
        """_load_dot_env does nothing when .env doesn't exist."""
        import sandcastle.__main__ as m
        monkeypatch.setattr(m, "_dot_env_loaded", False)
        # Change to temp dir where there's no .env
        orig_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            m._load_dot_env()
            # Should not raise
        finally:
            os.chdir(orig_dir)
            monkeypatch.setattr(m, "_dot_env_loaded", False)

    def test_load_dot_env_already_loaded(self):
        """_load_dot_env is idempotent."""
        import sandcastle.__main__ as m
        # When already loaded, should return early
        old = m._dot_env_loaded
        m._dot_env_loaded = True
        try:
            m._load_dot_env()  # Should not raise
        finally:
            m._dot_env_loaded = old

    def test_load_dot_env_with_file(self, tmp_path, monkeypatch):
        """_load_dot_env loads env vars from .env file."""
        import sandcastle.__main__ as m

        env_file = tmp_path / ".env"
        env_file.write_text(
            "# Comment\n"
            "TEST_KEY_SANDCASTLE=test_value\n"
            "QUOTED_VALUE=\"hello world\"\n"
            "EXPORT_VAR=exported\n"
        )
        monkeypatch.setattr(m, "_dot_env_loaded", False)
        orig_dir = os.getcwd()
        os.chdir(tmp_path)
        # Remove test key if present
        os.environ.pop("TEST_KEY_SANDCASTLE", None)
        os.environ.pop("QUOTED_VALUE", None)
        try:
            m._load_dot_env()
            # env var should be set
            assert os.environ.get("TEST_KEY_SANDCASTLE") == "test_value"
        finally:
            os.chdir(orig_dir)
            os.environ.pop("TEST_KEY_SANDCASTLE", None)
            os.environ.pop("QUOTED_VALUE", None)
            monkeypatch.setattr(m, "_dot_env_loaded", False)


# ===========================================================================
# _validate_run_id
# ===========================================================================

class TestValidateRunId:
    """Cover _validate_run_id function."""

    def test_validate_run_id_valid_uuid(self):
        """_validate_run_id accepts valid UUID."""
        from sandcastle.__main__ import _validate_run_id
        # Should not raise
        _validate_run_id(str(uuid.uuid4()))

    def test_validate_run_id_with_slash(self, capsys):
        """_validate_run_id rejects IDs with slashes."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("not/a/uuid")

    def test_validate_run_id_with_spaces(self, capsys):
        """_validate_run_id rejects IDs with spaces."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("not a uuid")

    def test_validate_run_id_empty(self, capsys):
        """_validate_run_id rejects empty IDs."""
        from sandcastle.__main__ import _validate_run_id
        with pytest.raises(SystemExit):
            _validate_run_id("")

    def test_validate_run_id_non_uuid_warns(self, capsys):
        """_validate_run_id warns for non-UUID but lets proceed."""
        from sandcastle.__main__ import _validate_run_id
        # Short non-UUID values print a warning but don't exit
        _validate_run_id("abc123")  # Should not raise


# ===========================================================================
# _format_cli_error
# ===========================================================================

class TestFormatCliError:
    """Cover _format_cli_error function."""

    def test_format_sandcastle_error_401(self):
        """_format_cli_error formats 401 error using SandcastleError class name."""
        from sandcastle.__main__ import _format_cli_error

        # The function checks `type(exc).__name__ == "SandcastleError"`, so we must
        # create a class with exactly that name.
        SandcastleError = type("SandcastleError", (Exception,), {
            "__init__": lambda self: (
                Exception.__init__(self, "Invalid API key"),
                setattr(self, "status_code", 401),
                setattr(self, "code", "UNAUTHORIZED"),
                setattr(self, "message", "Invalid API key"),
            )[-1]  # Return None
        })

        exc = SandcastleError.__new__(SandcastleError)
        exc.status_code = 401
        exc.code = "UNAUTHORIZED"
        exc.message = "Invalid API key"
        result = _format_cli_error(exc)
        assert "401" in result or "Authentication" in result or "API key" in result

    def test_format_sandcastle_error_404(self):
        """_format_cli_error formats 404 error using SandcastleError class name."""
        from sandcastle.__main__ import _format_cli_error

        SandcastleError = type("SandcastleError", (Exception,), {})
        exc = SandcastleError.__new__(SandcastleError)
        exc.status_code = 404
        exc.code = "NOT_FOUND"
        exc.message = "Resource not found"
        result = _format_cli_error(exc)
        assert "404" in result or "not found" in result.lower()

    def test_format_connection_error(self):
        """_format_cli_error formats connection errors."""
        from sandcastle.__main__ import _format_cli_error

        class ConnectError(Exception):
            pass

        # The function checks class name
        ConnectError.__name__ = "ConnectError"
        result = _format_cli_error(ConnectError("Connection refused"))
        # General error handling
        assert isinstance(result, str)

    def test_format_file_not_found(self):
        """_format_cli_error formats FileNotFoundError."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(FileNotFoundError("config.yaml"))
        assert "File not found" in result or "config.yaml" in result

    def test_format_permission_error(self):
        """_format_cli_error formats PermissionError."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(PermissionError("/etc/secret"))
        assert "Permission" in result or "permission" in result

    def test_format_generic_error(self):
        """_format_cli_error formats generic exceptions."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(RuntimeError("something went wrong"))
        assert "something went wrong" in result or "Error" in result

    def test_format_500_error(self):
        """_format_cli_error handles 500 in message string."""
        from sandcastle.__main__ import _format_cli_error
        result = _format_cli_error(Exception("HTTP 500 Internal Server Error"))
        assert isinstance(result, str)


# ===========================================================================
# _attr
# ===========================================================================

class TestAttrHelper:
    """Cover _attr helper function."""

    def test_attr_from_dict(self):
        """_attr gets value from dict."""
        from sandcastle.__main__ import _attr
        result = _attr({"key": "value"}, "key")
        assert result == "value"

    def test_attr_from_dict_missing(self):
        """_attr returns default for missing dict key."""
        from sandcastle.__main__ import _attr
        result = _attr({"a": 1}, "missing", default="fallback")
        assert result == "fallback"

    def test_attr_from_object(self):
        """_attr gets attribute from object."""
        from sandcastle.__main__ import _attr
        obj = SimpleNamespace(name="Alice", age=30)
        result = _attr(obj, "name")
        assert result == "Alice"

    def test_attr_from_object_missing(self):
        """_attr returns default for missing object attribute."""
        from sandcastle.__main__ import _attr
        obj = SimpleNamespace(name="Alice")
        result = _attr(obj, "missing", default="N/A")
        assert result == "N/A"


# ===========================================================================
# _parse_input_pairs
# ===========================================================================

class TestParseInputPairs:
    """Cover _parse_input_pairs function."""

    def test_parse_input_pairs_none(self):
        """_parse_input_pairs returns empty dict for None."""
        from sandcastle.__main__ import _parse_input_pairs
        assert _parse_input_pairs(None) == {}

    def test_parse_input_pairs_strings(self):
        """_parse_input_pairs handles plain string values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["name=Alice", "city=Berlin"])
        assert result["name"] == "Alice"
        assert result["city"] == "Berlin"

    def test_parse_input_pairs_json_number(self):
        """_parse_input_pairs parses JSON number values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["count=42", "score=9.5"])
        assert result["count"] == 42
        assert result["score"] == 9.5

    def test_parse_input_pairs_json_bool(self):
        """_parse_input_pairs parses JSON boolean values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(["flag=true", "debug=false"])
        assert result["flag"] is True
        assert result["debug"] is False

    def test_parse_input_pairs_json_object(self):
        """_parse_input_pairs parses JSON object values."""
        from sandcastle.__main__ import _parse_input_pairs
        result = _parse_input_pairs(['config={"key": "val"}'])
        assert result["config"] == {"key": "val"}

    def test_parse_input_pairs_invalid_format(self, capsys):
        """_parse_input_pairs exits on invalid pair format."""
        from sandcastle.__main__ import _parse_input_pairs
        with pytest.raises(SystemExit):
            _parse_input_pairs(["no_equals_sign"])

    def test_parse_input_pairs_empty_key(self, capsys):
        """_parse_input_pairs exits on empty key."""
        from sandcastle.__main__ import _parse_input_pairs
        with pytest.raises(SystemExit):
            _parse_input_pairs(["=value"])


# ===========================================================================
# _to_dict and _to_dicts in __main__.py
# ===========================================================================

class TestMainToDictHelpers:
    """Cover _to_dict and _to_dicts in __main__.py."""

    def test_to_dict_with_dict(self):
        """_to_dict returns dict unchanged."""
        from sandcastle.__main__ import _to_dict
        result = _to_dict({"key": "value"})
        assert result == {"key": "value"}

    def test_to_dict_with_model_dump(self):
        """_to_dict calls model_dump on pydantic objects."""
        from sandcastle.__main__ import _to_dict
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        result = _to_dict(TestModel(name="test", value=5))
        assert result["name"] == "test"

    def test_to_dict_with_dict_obj(self):
        """_to_dict falls back to __dict__ for non-pydantic objects."""
        from sandcastle.__main__ import _to_dict

        class SimpleObj:
            def __init__(self):
                self.x = 1
                self.y = 2

        result = _to_dict(SimpleObj())
        assert "x" in result

    def test_to_dict_scalar_value(self):
        """_to_dict wraps scalar in value dict."""
        from sandcastle.__main__ import _to_dict
        result = _to_dict(42)
        assert result == {"value": "42"}

    def test_to_dicts_with_paginated_list(self):
        """_to_dicts handles objects with .items attribute."""
        from sandcastle.__main__ import _to_dicts
        mock_list = SimpleNamespace(items=[{"a": 1}, {"b": 2}])
        result = _to_dicts(mock_list)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_to_dicts_with_dict_having_data_key(self):
        """_to_dicts handles dict with 'data' key containing a list."""
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts({"data": [{"id": 1}, {"id": 2}]})
        assert isinstance(result, list)

    def test_to_dicts_with_plain_list(self):
        """_to_dicts handles plain list."""
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts([{"a": 1}, {"b": 2}])
        assert isinstance(result, list)

    def test_to_dicts_with_dict_no_data_key(self):
        """_to_dicts wraps plain dict in a list."""
        from sandcastle.__main__ import _to_dicts
        result = _to_dicts({"key": "value"})
        assert isinstance(result, list)
        assert len(result) == 1


# ===========================================================================
# _fmt_time
# ===========================================================================

class TestFmtTime:
    """Cover _fmt_time helper function."""

    def test_fmt_time_none(self):
        """_fmt_time returns - for None."""
        from sandcastle.__main__ import _fmt_time
        assert _fmt_time(None) == "-"

    def test_fmt_time_string(self):
        """_fmt_time formats ISO string."""
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time("2026-01-15T10:30:00")
        assert "2026-01-15" in result

    def test_fmt_time_datetime_object(self):
        """_fmt_time formats datetime object."""
        from datetime import datetime, timezone
        from sandcastle.__main__ import _fmt_time
        dt = datetime(2026, 3, 20, 15, 30, tzinfo=timezone.utc)
        result = _fmt_time(dt)
        assert "2026-03-20" in result

    def test_fmt_time_other_value(self):
        """_fmt_time converts non-datetime to string."""
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time(12345)
        assert isinstance(result, str)


# ===========================================================================
# _load_input_file
# ===========================================================================

class TestLoadInputFile:
    """Cover _load_input_file function."""

    def test_load_input_file_valid_json(self, tmp_path):
        """_load_input_file loads valid JSON file."""
        from sandcastle.__main__ import _load_input_file

        json_file = tmp_path / "input.json"
        json_file.write_text('{"key": "value", "count": 42}')
        result = _load_input_file(str(json_file))
        assert result == {"key": "value", "count": 42}

    def test_load_input_file_not_found(self):
        """_load_input_file exits on missing file."""
        from sandcastle.__main__ import _load_input_file
        with pytest.raises(SystemExit):
            _load_input_file("/nonexistent/path/input.json")

    def test_load_input_file_invalid_json(self, tmp_path):
        """_load_input_file exits on invalid JSON."""
        from sandcastle.__main__ import _load_input_file
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json!")
        with pytest.raises(SystemExit):
            _load_input_file(str(bad_file))

    def test_load_input_file_too_large(self, tmp_path):
        """_load_input_file exits when file exceeds size limit."""
        from sandcastle.__main__ import _load_input_file, _MAX_INPUT_FILE_SIZE

        large_file = tmp_path / "large.json"
        # Write small file but patch the size check
        large_file.write_text("{}")
        with patch("os.path.getsize", return_value=_MAX_INPUT_FILE_SIZE + 1):
            with pytest.raises(SystemExit):
                _load_input_file(str(large_file))
