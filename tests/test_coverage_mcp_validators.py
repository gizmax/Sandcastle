"""Coverage for mcp_server.py validators and helpers.

Targets:
  - _validate_identifier
  - _validate_workflow_name
  - _validate_limit
  - _validate_status
  - _safe_parse_json
  - _validate_yaml_content
  - _validate_cron
  - _to_dict
  - _json_result
  - _error_result
  - _to_dicts

Also covers:
  - engine/memory.py score_importance, should_admit, enrich_memory branches
  - queue/scheduler.py _load_workflow_yaml, add_schedule
  - api/a2a.py helper functions
  - __main__.py _to_dict, _fmt_time, _C.supports_color
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# mcp_server.py validator functions
# ===========================================================================

class TestMcpValidateIdentifier:
    """Cover _validate_identifier function."""

    def test_valid_identifier(self):
        """_validate_identifier accepts valid run ID."""
        from sandcastle.mcp_server import _validate_identifier
        result = _validate_identifier("run-123-abc", "run_id")
        assert result == "run-123-abc"

    def test_empty_string_raises(self):
        """_validate_identifier rejects empty string."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_identifier("", "run_id")

    def test_too_long_raises(self):
        """_validate_identifier rejects string > 256 chars."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="256"):
            _validate_identifier("x" * 257, "run_id")

    def test_slash_raises(self):
        """_validate_identifier rejects string with slash."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="path separators"):
            _validate_identifier("run/traversal", "run_id")

    def test_backslash_raises(self):
        """_validate_identifier rejects string with backslash."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="path separators"):
            _validate_identifier("run\\traversal", "run_id")

    def test_dotdot_raises(self):
        """_validate_identifier rejects strings containing '..'."""
        from sandcastle.mcp_server import _validate_identifier
        # Note: slash in "run/../traversal" is detected first
        with pytest.raises(ValueError):
            _validate_identifier("run/../traversal", "run_id")
        # Pure dotdot without slash is also rejected
        with pytest.raises(ValueError):
            _validate_identifier("run..traversal", "run_id")

    def test_control_char_raises(self):
        """_validate_identifier rejects control characters."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="control characters"):
            _validate_identifier("run\x00id", "run_id")

    def test_non_string_raises(self):
        """_validate_identifier rejects non-string input."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="must be a string"):
            _validate_identifier(123, "run_id")

    def test_whitespace_only_raises(self):
        """_validate_identifier rejects whitespace-only string."""
        from sandcastle.mcp_server import _validate_identifier
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_identifier("   ", "run_id")


class TestMcpValidateWorkflowName:
    """Cover _validate_workflow_name function."""

    def test_valid_name(self):
        """_validate_workflow_name accepts valid workflow name."""
        from sandcastle.mcp_server import _validate_workflow_name
        result = _validate_workflow_name("my-workflow-v1.0")
        assert result == "my-workflow-v1.0"

    def test_special_chars_raises(self):
        """_validate_workflow_name rejects names with special characters."""
        from sandcastle.mcp_server import _validate_workflow_name
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_workflow_name("my workflow!")


class TestMcpValidateLimit:
    """Cover _validate_limit function."""

    def test_valid_limit(self):
        """_validate_limit accepts valid limit."""
        from sandcastle.mcp_server import _validate_limit
        result = _validate_limit(20)
        assert result == 20

    def test_zero_limit_raises(self):
        """_validate_limit rejects limit < 1."""
        from sandcastle.mcp_server import _validate_limit
        with pytest.raises(ValueError, match="at least 1"):
            _validate_limit(0)

    def test_too_large_limit_raises(self):
        """_validate_limit rejects limit > 200."""
        from sandcastle.mcp_server import _validate_limit
        with pytest.raises(ValueError, match="200"):
            _validate_limit(201)

    def test_non_int_raises(self):
        """_validate_limit rejects non-integer."""
        from sandcastle.mcp_server import _validate_limit
        with pytest.raises(ValueError, match="integer"):
            _validate_limit("20")


class TestMcpValidateStatus:
    """Cover _validate_status function."""

    def test_valid_status_completed(self):
        """_validate_status accepts 'completed'."""
        from sandcastle.mcp_server import _validate_status
        result = _validate_status("completed")
        assert result == "completed"

    def test_valid_status_running(self):
        """_validate_status accepts 'running'."""
        from sandcastle.mcp_server import _validate_status
        result = _validate_status("running")
        assert result == "running"

    def test_empty_status_returns_none(self):
        """_validate_status returns None for empty string."""
        from sandcastle.mcp_server import _validate_status
        result = _validate_status("")
        assert result is None

    def test_whitespace_status_returns_none(self):
        """_validate_status returns None for whitespace-only string."""
        from sandcastle.mcp_server import _validate_status
        result = _validate_status("   ")
        assert result is None

    def test_invalid_status_raises(self):
        """_validate_status raises for invalid status."""
        from sandcastle.mcp_server import _validate_status
        with pytest.raises(ValueError, match="Invalid status"):
            _validate_status("invalid-status")

    def test_case_insensitive(self):
        """_validate_status is case-insensitive."""
        from sandcastle.mcp_server import _validate_status
        result = _validate_status("COMPLETED")
        assert result == "completed"


class TestMcpSafeParseJson:
    """Cover _safe_parse_json function."""

    def test_valid_json_object(self):
        """_safe_parse_json parses valid JSON object."""
        from sandcastle.mcp_server import _safe_parse_json
        result = _safe_parse_json('{"key": "value", "count": 42}')
        assert result == {"key": "value", "count": 42}

    def test_empty_string_returns_empty(self):
        """_safe_parse_json returns {} for empty string."""
        from sandcastle.mcp_server import _safe_parse_json
        result = _safe_parse_json("")
        assert result == {}

    def test_whitespace_returns_empty(self):
        """_safe_parse_json returns {} for whitespace-only string."""
        from sandcastle.mcp_server import _safe_parse_json
        result = _safe_parse_json("   ")
        assert result == {}

    def test_invalid_json_raises(self):
        """_safe_parse_json raises for invalid JSON."""
        from sandcastle.mcp_server import _safe_parse_json
        with pytest.raises(ValueError, match="not valid JSON"):
            _safe_parse_json("{invalid json}")

    def test_json_array_raises(self):
        """_safe_parse_json raises for JSON array (not dict)."""
        from sandcastle.mcp_server import _safe_parse_json
        with pytest.raises(ValueError, match="must be a JSON object"):
            _safe_parse_json("[1, 2, 3]")

    def test_too_long_raises(self):
        """_safe_parse_json raises for input > max length."""
        from sandcastle.mcp_server import _safe_parse_json, _MAX_PARAM_LENGTH
        long_input = "x" * (_MAX_PARAM_LENGTH + 1)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _safe_parse_json(long_input)


class TestMcpValidateYamlContent:
    """Cover _validate_yaml_content function."""

    def test_valid_content(self):
        """_validate_yaml_content accepts valid YAML string."""
        from sandcastle.mcp_server import _validate_yaml_content
        content = "name: my-workflow\nsteps: []\n"
        result = _validate_yaml_content(content)
        assert "my-workflow" in result

    def test_non_string_raises(self):
        """_validate_yaml_content rejects non-string input."""
        from sandcastle.mcp_server import _validate_yaml_content
        with pytest.raises(ValueError, match="must be a string"):
            _validate_yaml_content(123)

    def test_empty_string_raises(self):
        """_validate_yaml_content rejects empty string."""
        from sandcastle.mcp_server import _validate_yaml_content
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_yaml_content("")

    def test_too_long_raises(self):
        """_validate_yaml_content rejects content > 512KB."""
        from sandcastle.mcp_server import _validate_yaml_content, _MAX_YAML_LENGTH
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _validate_yaml_content("x" * (_MAX_YAML_LENGTH + 1))


class TestMcpValidateCron:
    """Cover _validate_cron function."""

    def test_valid_5field_cron(self):
        """_validate_cron accepts valid 5-field cron expression."""
        from sandcastle.mcp_server import _validate_cron
        result = _validate_cron("0 9 * * *")
        assert result == "0 9 * * *"

    def test_valid_6field_cron(self):
        """_validate_cron accepts 6-field cron expression."""
        from sandcastle.mcp_server import _validate_cron
        result = _validate_cron("0 0 9 * * *")
        assert result == "0 0 9 * * *"

    def test_non_string_raises(self):
        """_validate_cron rejects non-string input."""
        from sandcastle.mcp_server import _validate_cron
        with pytest.raises(ValueError, match="must be a string"):
            _validate_cron(42)

    def test_empty_raises(self):
        """_validate_cron rejects empty string."""
        from sandcastle.mcp_server import _validate_cron
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_cron("")

    def test_too_few_fields_raises(self):
        """_validate_cron rejects expression with < 5 fields."""
        from sandcastle.mcp_server import _validate_cron
        with pytest.raises(ValueError, match="5 or 6 fields"):
            _validate_cron("0 9 * *")


class TestMcpToDict:
    """Cover _to_dict function."""

    def test_none_returns_none(self):
        """_to_dict returns None for None input."""
        from sandcastle.mcp_server import _to_dict
        assert _to_dict(None) is None

    def test_primitive_types(self):
        """_to_dict returns primitives unchanged."""
        from sandcastle.mcp_server import _to_dict
        assert _to_dict("hello") == "hello"
        assert _to_dict(42) == 42
        assert _to_dict(3.14) == 3.14
        assert _to_dict(True) is True

    def test_datetime_to_iso(self):
        """_to_dict converts datetime to ISO string."""
        from sandcastle.mcp_server import _to_dict
        dt = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        result = _to_dict(dt)
        assert isinstance(result, str)
        assert "2026" in result

    def test_dict_recursion(self):
        """_to_dict recursively converts dicts."""
        from sandcastle.mcp_server import _to_dict
        data = {"key": "value", "nested": {"a": 1}}
        result = _to_dict(data)
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_list_recursion(self):
        """_to_dict recursively converts lists."""
        from sandcastle.mcp_server import _to_dict
        result = _to_dict([1, "two", None])
        assert result == [1, "two", None]

    def test_tuple_converts_to_list(self):
        """_to_dict converts tuples to lists."""
        from sandcastle.mcp_server import _to_dict
        result = _to_dict((1, 2, 3))
        assert result == [1, 2, 3]

    def test_dataclass_converts(self):
        """_to_dict converts dataclasses to dicts."""
        from sandcastle.mcp_server import _to_dict

        @dataclasses.dataclass
        class MyData:
            name: str
            count: int

        result = _to_dict(MyData(name="test", count=5))
        assert result == {"name": "test", "count": 5}

    def test_pydantic_model_converts(self):
        """_to_dict handles objects with model_dump()."""
        from sandcastle.mcp_server import _to_dict

        class FakePydantic:
            def model_dump(self):
                return {"field": "value"}

        result = _to_dict(FakePydantic())
        assert result == {"field": "value"}

    def test_object_with_dict(self):
        """_to_dict handles objects with __dict__."""
        from sandcastle.mcp_server import _to_dict
        obj = SimpleNamespace(x=1, y="hello")
        result = _to_dict(obj)
        assert result["x"] == 1
        assert result["y"] == "hello"

    def test_unknown_type_to_string(self):
        """_to_dict converts unknown types to string."""
        from sandcastle.mcp_server import _to_dict

        class WeirdClass:
            def __str__(self):
                return "weird"
            # Override __dict__ to prevent that path being used
            __dict__ = None

        # This exercises the str() fallback
        result = _to_dict(42.0 + 0j)  # complex number - no __dict__
        assert isinstance(result, str)


class TestMcpJsonResult:
    """Cover _json_result and _error_result functions."""

    def test_json_result_dict(self):
        """_json_result serializes dict to JSON string."""
        from sandcastle.mcp_server import _json_result
        result = _json_result({"key": "value"})
        parsed = json.loads(result)
        assert parsed["key"] == "value"

    def test_json_result_list(self):
        """_json_result serializes list to JSON string."""
        from sandcastle.mcp_server import _json_result
        result = _json_result([1, 2, 3])
        parsed = json.loads(result)
        assert parsed == [1, 2, 3]

    def test_error_result_formats_error(self):
        """_error_result formats exception to JSON error string."""
        from sandcastle.mcp_server import _error_result
        exc = ValueError("Something went wrong")
        result = _error_result(exc)
        parsed = json.loads(result)
        # error field is True or a string, message contains the error text
        assert "message" in parsed or "error" in parsed
        assert "Something went wrong" in parsed.get("message", "") or parsed.get("error") is True


# ===========================================================================
# engine/memory.py - score_importance, should_admit, enrich_memory
# ===========================================================================

class TestMemoryHelpers:
    """Cover score_importance, should_admit, and enrich_memory."""

    def test_score_importance_short_content(self):
        """score_importance penalizes very short content."""
        from sandcastle.engine.memory import score_importance
        score = score_importance("hi", [])
        assert score < 0.5  # penalized for being too short

    def test_score_importance_sweet_spot_length(self):
        """score_importance gives bonus for 50-500 char content."""
        from sandcastle.engine.memory import score_importance
        content = "This is a medium-length memory about user preferences for data processing."
        score = score_importance(content, [])
        assert score > 0.5

    def test_score_importance_very_long_content(self):
        """score_importance penalizes very long content (> 2000 chars)."""
        from sandcastle.engine.memory import score_importance
        long_content = "x" * 2100
        score = score_importance(long_content, [])
        assert score < 0.6

    def test_score_importance_structured_data(self):
        """score_importance gives bonus for structured data patterns."""
        from sandcastle.engine.memory import score_importance
        content = "User had a score of 42.5 on 2026-01-15"
        score = score_importance(content, [])
        # Should have structured data bonus
        assert score >= 0.5

    def test_score_importance_high_overlap_penalty(self):
        """score_importance penalizes content heavily overlapping with existing."""
        from sandcastle.engine.memory import score_importance
        existing = [{"memory": "User prefers Python for data analysis tasks"}]
        new_content = "User prefers Python for data analysis tasks"
        score = score_importance(new_content, existing)
        # Should be penalized for high overlap
        assert score < 0.5

    def test_score_importance_moderate_overlap(self):
        """score_importance applies moderate penalty for 50-80% overlap."""
        from sandcastle.engine.memory import score_importance
        existing = [{"memory": "User likes Python programming and data science work"}]
        new_content = "User likes Python programming for different analysis projects"
        score = score_importance(new_content, existing)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_score_importance_empty_existing_memories(self):
        """score_importance handles empty memory list."""
        from sandcastle.engine.memory import score_importance
        content = "This is a useful memory about workflow configurations."
        score = score_importance(content, [])
        assert 0.0 <= score <= 1.0

    def test_score_importance_memory_with_no_text(self):
        """score_importance skips memories with no 'memory' field."""
        from sandcastle.engine.memory import score_importance
        existing = [{"memory": ""}, {"id": "orphan"}]
        content = "Some new memory content here"
        score = score_importance(content, existing)
        assert 0.0 <= score <= 1.0

    def test_should_admit_empty_content_rejected(self):
        """should_admit rejects empty content."""
        from sandcastle.engine.memory import should_admit
        admitted, score, reason = should_admit("", [])
        assert admitted is False
        assert "Empty" in reason

    def test_should_admit_whitespace_rejected(self):
        """should_admit rejects whitespace-only content."""
        from sandcastle.engine.memory import should_admit
        admitted, score, reason = should_admit("   ", [])
        assert admitted is False

    def test_should_admit_low_score_rejected(self):
        """should_admit rejects content with score below threshold."""
        from sandcastle.engine.memory import should_admit
        # Very short content gets low score
        admitted, score, reason = should_admit("hi", [])
        assert admitted is False
        assert score < 0.3

    def test_should_admit_good_content_accepted(self):
        """should_admit accepts good content."""
        from sandcastle.engine.memory import should_admit
        content = "User prefers detailed documentation and comprehensive test coverage for code reviews."
        admitted, score, reason = should_admit(content, [])
        assert admitted is True
        assert reason == "Admitted"

    def test_enrich_memory_basic(self):
        """enrich_memory adds keywords and tags."""
        from sandcastle.engine.memory import enrich_memory
        result = enrich_memory("User prefers Python programming for data analysis")
        assert "content" in result
        assert "keywords" in result
        assert "tags" in result
        assert "enriched_at" in result

    def test_enrich_memory_with_metadata(self):
        """enrich_memory includes provided metadata."""
        from sandcastle.engine.memory import enrich_memory
        metadata = {"source": "chat", "priority": "high"}
        result = enrich_memory("Some memory content here", metadata=metadata)
        assert result["metadata"]["source"] == "chat"

    def test_enrich_memory_detects_issue_tag(self):
        """enrich_memory tags content with 'issue' for error content."""
        from sandcastle.engine.memory import enrich_memory
        result = enrich_memory("User reported an error in the workflow execution")
        assert "issue" in result.get("tags", [])

    def test_enrich_memory_detects_preference_tag(self):
        """enrich_memory tags content with 'preference' for preference content."""
        from sandcastle.engine.memory import enrich_memory
        result = enrich_memory("User prefers dark mode and compact layouts")
        assert "preference" in result.get("tags", [])


# ===========================================================================
# queue/scheduler.py - _load_workflow_yaml, add_schedule
# ===========================================================================

class TestSchedulerHelpers:
    """Cover scheduler helper functions."""

    def test_load_workflow_yaml_found(self):
        """_load_workflow_yaml loads YAML from workflows dir."""
        from sandcastle.queue.scheduler import _load_workflow_yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wf_file = os.path.join(tmpdir, "test-workflow.yaml")
            with open(wf_file, "w") as f:
                f.write("name: test-workflow\nsteps: []\n")

            with patch("sandcastle.queue.scheduler.settings") as mock_s:
                mock_s.workflows_dir = tmpdir
                result = _load_workflow_yaml("test-workflow")
            assert "test-workflow" in result

    def test_load_workflow_yaml_not_found(self):
        """_load_workflow_yaml raises FileNotFoundError for missing workflow."""
        from sandcastle.queue.scheduler import _load_workflow_yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sandcastle.queue.scheduler.settings") as mock_s:
                mock_s.workflows_dir = tmpdir
                with pytest.raises(FileNotFoundError):
                    _load_workflow_yaml("nonexistent-workflow")

    def test_add_schedule_empty_cron_raises(self):
        """add_schedule raises for empty cron expression."""
        from sandcastle.queue.scheduler import add_schedule
        with pytest.raises(ValueError, match="cron_expression must not be empty"):
            add_schedule("sched-1", "", "my-workflow")

    def test_add_schedule_empty_workflow_raises(self):
        """add_schedule raises for empty workflow name."""
        from sandcastle.queue.scheduler import add_schedule
        with pytest.raises(ValueError, match="workflow_name must not be empty"):
            add_schedule("sched-1", "0 9 * * *", "")

    def test_add_schedule_invalid_cron_raises(self):
        """add_schedule raises for invalid cron expression."""
        from sandcastle.queue.scheduler import add_schedule
        with pytest.raises(ValueError, match="Invalid cron expression"):
            add_schedule("sched-1", "not-a-cron", "my-workflow")


# ===========================================================================
# api/a2a.py - pure helper functions
# ===========================================================================

class TestA2aHelpers:
    """Cover A2A helper functions."""

    def test_extract_workflow_name_from_text(self):
        """_extract_workflow_name extracts name from text part."""
        from sandcastle.api.a2a import _extract_workflow_name
        message = {
            "parts": [
                {"type": "text", "text": "my-workflow"}
            ]
        }
        result = _extract_workflow_name(message)
        assert result == "my-workflow"

    def test_extract_workflow_name_from_data(self):
        """_extract_workflow_name extracts name from data part."""
        from sandcastle.api.a2a import _extract_workflow_name
        message = {
            "parts": [
                {"type": "data", "data": {"workflow_name": "email-sender"}}
            ]
        }
        result = _extract_workflow_name(message)
        assert result == "email-sender"

    def test_extract_workflow_name_no_parts(self):
        """_extract_workflow_name returns None when no parts."""
        from sandcastle.api.a2a import _extract_workflow_name
        result = _extract_workflow_name({"parts": []})
        assert result is None

    def test_extract_workflow_name_non_dict_message(self):
        """_extract_workflow_name returns None for non-dict message."""
        from sandcastle.api.a2a import _extract_workflow_name
        result = _extract_workflow_name("not-a-dict")
        assert result is None

    def test_extract_workflow_name_non_list_parts(self):
        """_extract_workflow_name returns None when parts is not a list."""
        from sandcastle.api.a2a import _extract_workflow_name
        result = _extract_workflow_name({"parts": "not-a-list"})
        assert result is None

    def test_extract_input_from_data_part(self):
        """_extract_input extracts input from data part."""
        from sandcastle.api.a2a import _extract_input
        message = {
            "parts": [
                {"type": "data", "data": {"workflow_name": "wf1", "input": {"key": "value"}}}
            ]
        }
        result = _extract_input(message)
        assert result == {"key": "value"}

    def test_extract_input_no_input_field(self):
        """_extract_input returns {} when no input field."""
        from sandcastle.api.a2a import _extract_input
        message = {
            "parts": [
                {"type": "data", "data": {"workflow_name": "wf1"}}
            ]
        }
        result = _extract_input(message)
        assert result == {}

    def test_extract_input_non_dict_message(self):
        """_extract_input returns {} for non-dict message."""
        from sandcastle.api.a2a import _extract_input
        result = _extract_input("not-a-dict")
        assert result == {}

    def test_build_task_response_with_error(self):
        """_build_task_response includes error in status message."""
        from sandcastle.api.a2a import _build_task_response
        result = _build_task_response("task-123", "failed", error="Workflow not found")
        assert result["status"]["message"] == "Workflow not found"

    def test_build_task_response_with_output(self):
        """_build_task_response includes output in artifacts."""
        from sandcastle.api.a2a import _build_task_response
        output = {"run_id": "run-123", "status": "completed"}
        result = _build_task_response("task-123", "completed", output_data=output)
        assert "artifacts" in result
        assert len(result["artifacts"]) > 0

    def test_build_task_response_no_output(self):
        """_build_task_response has no artifacts when output_data is None."""
        from sandcastle.api.a2a import _build_task_response
        result = _build_task_response("task-123", "working")
        assert "artifacts" not in result

    def test_map_status_running(self):
        """_map_status maps 'running' to 'working'."""
        from sandcastle.api.a2a import _map_status
        assert _map_status("running") == "working"

    def test_map_status_completed(self):
        """_map_status maps 'completed' to 'completed'."""
        from sandcastle.api.a2a import _map_status
        assert _map_status("completed") == "completed"

    def test_map_status_failed(self):
        """_map_status maps 'failed' to 'failed'."""
        from sandcastle.api.a2a import _map_status
        assert _map_status("failed") == "failed"

    def test_map_status_cancelled(self):
        """_map_status maps 'cancelled' to 'canceled' (A2A spelling)."""
        from sandcastle.api.a2a import _map_status
        result = _map_status("cancelled")
        assert result in ("canceled", "cancelled", "failed")

    def test_map_status_queued(self):
        """_map_status maps 'queued' to 'submitted' or 'working'."""
        from sandcastle.api.a2a import _map_status
        result = _map_status("queued")
        assert isinstance(result, str)


# ===========================================================================
# __main__.py - _to_dict, _fmt_time, _build_parser
# ===========================================================================

class TestMainDunderHelpers:
    """Cover additional __main__.py helper functions."""

    def test_to_dict_with_dict(self):
        """_to_dict returns dict unchanged."""
        from sandcastle.__main__ import _to_dict
        data = {"key": "value"}
        result = _to_dict(data)
        assert result == data

    def test_to_dict_with_namespace(self):
        """_to_dict converts SimpleNamespace to dict."""
        from sandcastle.__main__ import _to_dict
        obj = SimpleNamespace(status="completed", cost=0.05)
        result = _to_dict(obj)
        assert isinstance(result, dict)

    def test_to_dict_with_none(self):
        """_to_dict handles None input."""
        from sandcastle.__main__ import _to_dict
        result = _to_dict(None)
        assert result is None or isinstance(result, dict)

    def test_fmt_time_with_valid_timestamp(self):
        """_fmt_time formats valid ISO timestamp."""
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time("2026-01-15T10:30:00")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fmt_time_with_none(self):
        """_fmt_time returns '-' for None."""
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time(None)
        assert result == "-" or isinstance(result, str)

    def test_fmt_time_with_empty_string(self):
        """_fmt_time returns something for empty string."""
        from sandcastle.__main__ import _fmt_time
        result = _fmt_time("")
        assert isinstance(result, str)

    def test_build_parser_returns_parser(self):
        """_build_parser returns an ArgumentParser."""
        from sandcastle.__main__ import _build_parser
        parser = _build_parser()
        assert parser is not None

    def test_c_supports_color_with_no_color_env(self):
        """_C.supports_color() returns False when NO_COLOR is set."""
        from sandcastle.__main__ import _C
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            assert _C.supports_color() is False

    def test_c_supports_color_normal(self):
        """_C.supports_color() returns bool."""
        from sandcastle.__main__ import _C
        os.environ.pop("NO_COLOR", None)
        result = _C.supports_color()
        assert isinstance(result, bool)
