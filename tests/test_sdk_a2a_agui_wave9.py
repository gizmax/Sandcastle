"""Wave 9 deep audit tests for SDK, A2A, and AG-UI.

Tests cover previously uncovered paths:
  SDK:
    - base_url trailing slash normalization
    - _extract_data 204 No Content handling
    - _extract_data empty body handling
    - _extract_data structured error parsing branches
    - _parse_sse_lines SSE spec compliance (single leading space)
    - _parse_sse_lines multi-data-line events
    - _parse_sse_lines non-JSON data fallback
    - _parse_sse_lines flush on stream end without trailing blank line
    - _parse_sse_lines event type field
    - Context manager cleanup (sync and async)
    - Sync poll_until_done timeout
    - Async poll_until_done timeout
    - SandcastleError repr
    - _parse_datetime edge cases
    - _parse_run with all optional fields populated
    - _parse_run_list_item defaults
    - _parse_schedule defaults
    - _parse_health defaults
    - _parse_runtime defaults
    - _parse_stats defaults
    - PaginatedList defaults
    - list_runs with filters
    - list_approvals with filters
    - list_schedules
    - stream() connection error / timeout handling
    - stream() mid-stream error (StreamError)
    - Async stream() connection error / timeout handling

  A2A:
    - Batch request rejection (array body)
    - JSON-RPC notification (no id field) handling
    - tasks/send with control characters in workflow name
    - tasks/get with non-UUID task id
    - tasks/cancel on non-cancellable states
    - _map_status for unknown statuses
    - _extract_workflow_name with non-dict message
    - _extract_workflow_name with non-list parts
    - _extract_input with non-dict message
    - _build_task_response with error message
    - Agent card authentication field with auth required

  AG-UI:
    - _emit_terminal_event dispatches correctly
    - awaiting_approval emits run_awaiting_input
    - budget_exceeded emits run_error
    - cancelled emits run_finished
    - _map_event_bus_event step.failed
    - _map_event_bus_event step.completed with dict output
    - _map_event_bus_event run.completed
    - _agui_event timestamp format
    - Stream for running run emits run_started
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest

from sandcastle.sdk import (
    AsyncSandcastleClient,
    Approval,
    HealthStatus,
    PaginatedList,
    Run,
    RuntimeInfo,
    RunListItem,
    Schedule,
    SandcastleClient,
    SandcastleError,
    Stats,
    Step,
    Workflow,
    _extract_data,
    _parse_approval,
    _parse_datetime,
    _parse_health,
    _parse_run,
    _parse_run_list_item,
    _parse_runtime,
    _parse_schedule,
    _parse_sse_lines,
    _parse_stats,
    _parse_step,
    _parse_workflow,
    _validate_pagination,
    _validate_path_param,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
    content: bytes = b"",
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
        resp.text = json.dumps(json_data) if not text else text
    elif not text:
        resp.json.side_effect = Exception("No JSON")
    else:
        try:
            resp.json.return_value = json.loads(text)
        except Exception:
            resp.json.side_effect = Exception("No JSON")
    resp.content = content
    return resp


# ===================================================================
# SDK: base_url normalization
# ===================================================================


class TestBaseUrlNormalization:
    """Verify that trailing slashes on base_url are stripped."""

    def test_sync_client_strips_trailing_slash(self):
        c = SandcastleClient(base_url="http://localhost:8080/")
        assert c._base_url == "http://localhost:8080"
        c.close()

    def test_sync_client_strips_multiple_trailing_slashes(self):
        c = SandcastleClient(base_url="http://example.com///")
        assert c._base_url == "http://example.com"
        c.close()

    def test_sync_client_no_trailing_slash_unchanged(self):
        c = SandcastleClient(base_url="http://localhost:9090")
        assert c._base_url == "http://localhost:9090"
        c.close()

    @pytest.mark.asyncio
    async def test_async_client_strips_trailing_slash(self):
        c = AsyncSandcastleClient(base_url="http://localhost:8080/")
        assert c._base_url == "http://localhost:8080"
        await c.close()

    def test_repr_shows_normalized_url(self):
        c = SandcastleClient(base_url="http://localhost:8080/")
        assert "http://localhost:8080'" in repr(c)
        c.close()


# ===================================================================
# SDK: _extract_data
# ===================================================================


class TestExtractData:
    """Cover _extract_data edge cases."""

    def test_204_no_content(self):
        resp = _mock_response(status_code=204, text="")
        result = _extract_data(resp)
        assert result == {}

    def test_empty_text_body_raises(self):
        """A 200 with empty text and no parseable JSON should raise."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = ""
        resp.json.side_effect = Exception("No JSON")
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.code == "INVALID_RESPONSE"

    def test_error_with_nested_detail(self):
        resp = _mock_response(
            status_code=400,
            json_data={
                "detail": {
                    "error": {"code": "VALIDATION_ERROR", "message": "Bad input"}
                }
            },
        )
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.code == "VALIDATION_ERROR"
        assert exc_info.value.message == "Bad input"
        assert exc_info.value.status_code == 400

    def test_error_without_detail_key(self):
        resp = _mock_response(
            status_code=500,
            json_data={"error": {"code": "INTERNAL", "message": "Oops"}},
        )
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.code == "INTERNAL"

    def test_error_with_non_dict_body(self):
        resp = _mock_response(status_code=422, text="plain text error")
        resp.json.side_effect = Exception("not json")
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.code == "API_ERROR"
        assert "plain text error" in exc_info.value.message

    def test_error_with_empty_text(self):
        resp = _mock_response(status_code=500, text="")
        resp.json.side_effect = Exception("not json")
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.message == "Unknown API error"

    def test_success_non_json_raises(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "<html>not json</html>"
        resp.json.side_effect = Exception("not json")
        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)
        assert exc_info.value.code == "INVALID_RESPONSE"

    def test_success_with_data_key(self):
        resp = _mock_response(
            status_code=200,
            json_data={"data": {"run_id": "abc"}},
            text='{"data": {"run_id": "abc"}}',
        )
        result = _extract_data(resp)
        assert result == {"run_id": "abc"}

    def test_success_without_data_key(self):
        resp = _mock_response(
            status_code=200,
            json_data={"run_id": "abc", "status": "ok"},
            text='{"run_id": "abc", "status": "ok"}',
        )
        result = _extract_data(resp)
        assert result["run_id"] == "abc"


# ===================================================================
# SDK: SSE parsing
# ===================================================================


class TestSseParser:
    """Cover SSE parsing edge cases and spec compliance."""

    def test_single_leading_space_stripped(self):
        """Per SSE spec, only one leading space after colon is removed."""
        raw = 'data: {"key": "value"}\n\n'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["key"] == "value"

    def test_no_leading_space(self):
        """data:payload with no space after colon."""
        raw = 'data:{"key": "value"}\n\n'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["key"] == "value"

    def test_multi_data_lines(self):
        """Multiple data: lines should be concatenated with newlines."""
        raw = 'data: {"key":\ndata:  "value"}\n\n'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["key"] == "value"

    def test_event_type_field(self):
        """event: line sets _event on the parsed dict."""
        raw = 'event: status\ndata: {"ok": true}\n\n'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["_event"] == "status"
        assert events[0]["ok"] is True

    def test_event_type_no_space(self):
        """event:type with no space after colon."""
        raw = 'event:step\ndata: {"ok": true}\n\n'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["_event"] == "step"

    def test_non_json_data_becomes_raw(self):
        """Non-JSON data is wrapped in a dict with 'raw' key."""
        raw = "data: not json at all\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["raw"] == "not json at all"
        assert events[0]["_event"] == ""

    def test_flush_without_trailing_blank_line(self):
        """If stream ends without a blank line, remaining data is flushed."""
        raw = 'data: {"flushed": true}'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["flushed"] is True

    def test_multiple_events(self):
        """Multiple events separated by blank lines."""
        raw = (
            'event: a\ndata: {"n": 1}\n\n'
            'event: b\ndata: {"n": 2}\n\n'
        )
        events = list(_parse_sse_lines(raw))
        assert len(events) == 2
        assert events[0]["_event"] == "a"
        assert events[0]["n"] == 1
        assert events[1]["_event"] == "b"
        assert events[1]["n"] == 2

    def test_empty_lines_between_events(self):
        """Extra blank lines (no data) are ignored."""
        raw = 'data: {"n": 1}\n\n\n\ndata: {"n": 2}\n\n'
        events = list(_parse_sse_lines(raw))
        assert len(events) == 2

    def test_event_type_resets_between_events(self):
        """event type should reset between events."""
        raw = 'event: first\ndata: {"n": 1}\n\ndata: {"n": 2}\n\n'
        events = list(_parse_sse_lines(raw))
        assert events[0]["_event"] == "first"
        assert events[1]["_event"] == ""


# ===================================================================
# SDK: _parse_datetime
# ===================================================================


class TestParseDatetime:
    """Cover _parse_datetime edge cases."""

    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self):
        dt = datetime(2025, 1, 15, 12, 0, 0)
        assert _parse_datetime(dt) is dt

    def test_iso_string(self):
        dt = _parse_datetime("2025-01-15T12:00:00")
        assert dt is not None
        assert dt.year == 2025

    def test_invalid_string_returns_none(self):
        assert _parse_datetime("not a date") is None

    def test_integer_returns_none(self):
        # int cannot be parsed as ISO date
        assert _parse_datetime(12345) is None


# ===================================================================
# SDK: Parse functions with all optional fields
# ===================================================================


class TestParseFunctions:
    """Cover parse functions with various input shapes."""

    def test_parse_step_full(self):
        step = _parse_step({
            "step_id": "s1",
            "status": "completed",
            "parallel_index": 2,
            "output": {"result": 42},
            "cost_usd": 0.05,
            "duration_seconds": 1.5,
            "attempt": 3,
            "error": "some error",
        })
        assert step.step_id == "s1"
        assert step.parallel_index == 2
        assert step.attempt == 3
        assert step.error == "some error"

    def test_parse_step_defaults(self):
        step = _parse_step({})
        assert step.step_id == ""
        assert step.status == "unknown"
        assert step.parallel_index is None
        assert step.cost_usd == 0.0
        assert step.attempt == 1
        assert step.error is None

    def test_parse_run_full(self):
        run = _parse_run({
            "run_id": "r1",
            "status": "completed",
            "workflow_name": "wf",
            "input_data": {"key": "val"},
            "outputs": {"o": 1},
            "total_cost_usd": 0.10,
            "max_cost_usd": 5.0,
            "started_at": "2025-01-15T12:00:00",
            "completed_at": "2025-01-15T12:05:00",
            "error": None,
            "steps": [{"step_id": "s1", "status": "ok"}],
            "parent_run_id": "pr1",
            "replay_from_step": "s0",
            "fork_changes": {"model": "opus"},
            "depth": 2,
            "sub_workflow_of_step": "parent_step",
            "sub_runs": [{"id": "sub1"}],
            "new_run_id": "new1",
            "fork_from_step": "f1",
            "changes": {"prompt": "new"},
        })
        assert run.run_id == "r1"
        assert run.depth == 2
        assert run.parent_run_id == "pr1"
        assert run.new_run_id == "new1"
        assert len(run.steps) == 1

    def test_parse_run_new_run_id_fallback(self):
        """run_id falls back to new_run_id when run_id is missing."""
        run = _parse_run({"new_run_id": "fallback-id", "status": "ok"})
        assert run.run_id == "fallback-id"

    def test_parse_run_list_item_defaults(self):
        item = _parse_run_list_item({})
        assert item.run_id == ""
        assert item.workflow_name == ""
        assert item.status == "unknown"
        assert item.total_cost_usd == 0.0
        assert item.parent_run_id is None

    def test_parse_workflow_defaults(self):
        wf = _parse_workflow({})
        assert wf.name == ""
        assert wf.steps_count == 0
        assert wf.file_name == ""

    def test_parse_schedule_full(self):
        sched = _parse_schedule({
            "id": "sc1",
            "workflow_name": "wf",
            "cron_expression": "0 * * * *",
            "input_data": {"k": "v"},
            "enabled": False,
            "last_run_id": "lr1",
            "created_at": "2025-01-01T00:00:00",
        })
        assert sched.id == "sc1"
        assert sched.enabled is False
        assert sched.last_run_id == "lr1"

    def test_parse_health_defaults(self):
        h = _parse_health({})
        assert h.status == "unknown"
        assert h.runtime is False
        assert h.database is False
        assert h.redis is None

    def test_parse_runtime_defaults(self):
        rt = _parse_runtime({})
        assert rt.mode == "unknown"
        assert rt.sandbox_backend == ""
        assert rt.license is None
        assert rt.data_dir is None

    def test_parse_stats_defaults(self):
        st = _parse_stats({})
        assert st.total_runs_today == 0
        assert st.success_rate == 0.0
        assert st.runs_by_day == []
        assert st.cost_by_workflow == []

    def test_parse_approval_full(self):
        a = _parse_approval({
            "id": "a1",
            "run_id": "r1",
            "step_id": "s1",
            "status": "pending",
            "request_data": {"d": 1},
            "response_data": {"r": 2},
            "message": "Please approve",
            "reviewer_id": "rev1",
            "reviewer_comment": "LGTM",
            "timeout_at": "2025-02-01T00:00:00",
            "on_timeout": "skip",
            "allow_edit": True,
            "created_at": "2025-01-15T00:00:00",
            "resolved_at": "2025-01-16T00:00:00",
        })
        assert a.id == "a1"
        assert a.allow_edit is True
        assert a.on_timeout == "skip"
        assert a.reviewer_comment == "LGTM"
        assert a.timeout_at is not None

    def test_parse_approval_defaults(self):
        a = _parse_approval({})
        assert a.id == ""
        assert a.message == ""
        assert a.on_timeout == "abort"
        assert a.allow_edit is False


# ===================================================================
# SDK: SandcastleError
# ===================================================================


class TestSandcastleError:
    def test_repr(self):
        err = SandcastleError(404, "NOT_FOUND", "Not found")
        assert "[404]" in str(err)
        assert "NOT_FOUND" in str(err)
        assert "Not found" in str(err)

    def test_attributes(self):
        err = SandcastleError(429, "RATE_LIMITED", "Too many requests")
        assert err.status_code == 429
        assert err.code == "RATE_LIMITED"
        assert err.message == "Too many requests"


# ===================================================================
# SDK: Validation helpers
# ===================================================================


class TestValidation:
    def test_validate_pagination_zero_limit(self):
        with pytest.raises(ValueError, match="positive integer"):
            _validate_pagination(0, 0)

    def test_validate_pagination_negative_offset(self):
        with pytest.raises(ValueError, match="non-negative"):
            _validate_pagination(10, -1)

    def test_validate_pagination_over_max(self):
        with pytest.raises(ValueError, match="must not exceed 200"):
            _validate_pagination(201, 0)

    def test_validate_path_param_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_path_param("", "test")

    def test_validate_path_param_whitespace(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_path_param("   ", "test")

    def test_validate_path_param_slash(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_path_param("a/b", "test")

    def test_validate_path_param_backslash(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_path_param("a\\b", "test")

    def test_validate_path_param_valid(self):
        result = _validate_path_param("valid-id", "test")
        assert result == "valid-id"

    def test_validate_path_param_non_string(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_path_param(123, "test")  # type: ignore


# ===================================================================
# SDK: Context manager cleanup
# ===================================================================


class TestContextManager:
    def test_sync_context_manager_closes_client(self):
        with SandcastleClient(base_url="http://localhost:9999") as c:
            assert c._client is not None
        # After exiting context, _client.close() was called
        assert c._client.is_closed

    @pytest.mark.asyncio
    async def test_async_context_manager_closes_client(self):
        async with AsyncSandcastleClient(base_url="http://localhost:9999") as c:
            assert c._client is not None
        assert c._client.is_closed


# ===================================================================
# SDK: Sync client repr
# ===================================================================


class TestClientRepr:
    def test_sync_repr_with_key(self):
        c = SandcastleClient(api_key="test-key-placeholder")
        assert "api_key=***" in repr(c)
        c.close()

    def test_sync_repr_without_key(self):
        c = SandcastleClient()
        assert "api_key=None" in repr(c)
        c.close()

    @pytest.mark.asyncio
    async def test_async_repr_with_key(self):
        c = AsyncSandcastleClient(api_key="test-key-placeholder")
        assert "api_key=***" in repr(c)
        await c.close()

    @pytest.mark.asyncio
    async def test_async_repr_without_key(self):
        c = AsyncSandcastleClient()
        assert "api_key=None" in repr(c)
        await c.close()


# ===================================================================
# SDK: poll_until_done timeout
# ===================================================================


class TestPollTimeout:
    def test_sync_poll_timeout(self):
        c = SandcastleClient()
        # Mock get_run to always return running status
        running_run = Run(run_id="r1", status="running")
        with patch.object(c, "get_run", return_value=running_run):
            with pytest.raises(TimeoutError, match="did not reach terminal"):
                c._poll_until_done("r1", poll_interval=0.01, max_wait=0.05)
        c.close()

    @pytest.mark.asyncio
    async def test_async_poll_timeout(self):
        c = AsyncSandcastleClient()
        running_run = Run(run_id="r1", status="running")
        with patch.object(c, "get_run", new_callable=AsyncMock, return_value=running_run):
            with pytest.raises(TimeoutError, match="did not reach terminal"):
                await c._poll_until_done("r1", poll_interval=0.01, max_wait=0.05)
        await c.close()


# ===================================================================
# SDK: stream() error handling
# ===================================================================


class TestStreamErrorHandling:
    def test_sync_stream_connect_error(self):
        c = SandcastleClient(base_url="http://localhost:1")
        with pytest.raises(SandcastleError) as exc_info:
            list(c.stream("valid-run-id"))
        assert exc_info.value.code == "CONNECTION_ERROR"
        c.close()

    def test_sync_stream_mid_stream_error(self):
        """StreamError mid-stream yields a stream_error event."""
        c = SandcastleClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        def mock_iter_lines():
            yield 'event: status'
            yield 'data: {"status": "running"}'
            yield ''  # flush event
            raise httpx.StreamError("Connection lost")

        mock_resp.iter_lines = mock_iter_lines
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(c._client, "stream", return_value=mock_resp):
            events = list(c.stream("valid-run-id"))

        assert len(events) == 2
        assert events[0]["_event"] == "status"
        assert events[1]["_event"] == "stream_error"
        assert "Connection lost" in events[1]["message"]
        c.close()

    def test_sync_stream_http_error_raises(self):
        """HTTP error status on stream response raises SandcastleError."""
        c = SandcastleClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = '{"detail": {"error": {"code": "NOT_FOUND", "message": "Run not found"}}}'
        mock_resp.json.return_value = {
            "detail": {"error": {"code": "NOT_FOUND", "message": "Run not found"}}
        }
        mock_resp.read = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(c._client, "stream", return_value=mock_resp):
            with pytest.raises(SandcastleError) as exc_info:
                list(c.stream("valid-run-id"))
            assert exc_info.value.code == "NOT_FOUND"
        c.close()


# ===================================================================
# SDK: PaginatedList
# ===================================================================


class TestPaginatedList:
    def test_defaults(self):
        p = PaginatedList(items=[])
        assert p.total == 0
        assert p.limit == 50
        assert p.offset == 0

    def test_custom_values(self):
        p = PaginatedList(items=[1, 2, 3], total=100, limit=10, offset=20)
        assert len(p.items) == 3
        assert p.total == 100


# ===================================================================
# SDK: Sync client list methods
# ===================================================================


class TestSyncListMethods:
    def test_list_runs_with_filters(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={
                "data": [
                    {"run_id": "r1", "workflow_name": "w1", "status": "completed"},
                ],
                "meta": {"total": 1, "limit": 10, "offset": 0},
            },
            text='{"data": [{"run_id": "r1", "workflow_name": "w1", "status": "completed"}], "meta": {"total": 1, "limit": 10, "offset": 0}}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.list_runs(status="completed", workflow="w1", limit=10, offset=0)
        assert result.total == 1
        assert len(result.items) == 1
        assert isinstance(result.items[0], RunListItem)
        c.close()

    def test_list_workflows_empty(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": []},
            text='{"data": []}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.list_workflows()
        assert result == []
        c.close()

    def test_list_workflows_non_list(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": "not a list"},
            text='{"data": "not a list"}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.list_workflows()
        assert result == []
        c.close()

    def test_list_approvals_with_status_filter(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={
                "data": [
                    {"id": "a1", "run_id": "r1", "step_id": "s1", "status": "pending"},
                ],
                "meta": {"total": 1, "limit": 50, "offset": 0},
            },
            text='{"data": [{"id": "a1", "run_id": "r1", "step_id": "s1", "status": "pending"}], "meta": {"total": 1}}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.list_approvals(status="pending")
        assert result.total == 1
        assert isinstance(result.items[0], Approval)
        c.close()

    def test_list_schedules(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={
                "data": [
                    {"id": "s1", "workflow_name": "w1", "cron_expression": "0 * * * *"},
                ],
                "meta": {"total": 1, "limit": 50, "offset": 0},
            },
            text='{"data": [{"id": "s1", "workflow_name": "w1", "cron_expression": "0 * * * *"}], "meta": {"total": 1}}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.list_schedules()
        assert result.total == 1
        assert isinstance(result.items[0], Schedule)
        c.close()


# ===================================================================
# SDK: Sync client run with wait=True
# ===================================================================


class TestRunWithWait:
    def test_run_wait_polls_until_completed(self):
        c = SandcastleClient()
        run_resp = _mock_response(
            json_data={"data": {"run_id": "r1", "status": "running"}},
            text='{"data": {"run_id": "r1", "status": "running"}}',
        )
        completed_resp = _mock_response(
            json_data={"data": {"run_id": "r1", "status": "completed"}},
            text='{"data": {"run_id": "r1", "status": "completed"}}',
        )

        post_mock = MagicMock(return_value=run_resp)
        get_mock = MagicMock(return_value=completed_resp)

        with patch.object(c._client, "post", post_mock):
            with patch.object(c._client, "get", get_mock):
                with patch("sandcastle.sdk.time.sleep"):
                    result = c.run("my-workflow", wait=True, poll_interval=0.01)

        assert result.status == "completed"
        c.close()


# ===================================================================
# A2A: JSON-RPC batch request rejection
# ===================================================================


class TestA2ABatchRequests:
    """Test that JSON-RPC batch requests (arrays) are properly rejected."""

    def test_batch_array_rejected(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json=[
            {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {"id": str(uuid.uuid4())}},
            {"jsonrpc": "2.0", "id": "2", "method": "tasks/get", "params": {"id": str(uuid.uuid4())}},
        ])
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600
        assert "batch" in data["error"]["message"].lower()


# ===================================================================
# A2A: JSON-RPC notification (no id field)
# ===================================================================


class TestA2ANotification:
    """Test JSON-RPC notification handling (requests without id field)."""

    def test_notification_without_id_field(self):
        """A request without an id field is a notification per JSON-RPC 2.0."""
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "params": {"id": str(uuid.uuid4())},
        })
        assert response.status_code == 200
        data = response.json()
        # id should be None (absent from request -> None in response)
        assert data.get("id") is None


# ===================================================================
# A2A: _map_status
# ===================================================================


class TestA2AMapStatus:
    def test_known_statuses(self):
        from sandcastle.api.a2a import _map_status
        assert _map_status("queued") == "submitted"
        assert _map_status("running") == "working"
        assert _map_status("completed") == "completed"
        assert _map_status("failed") == "failed"
        assert _map_status("cancelled") == "canceled"
        assert _map_status("partial") == "completed"
        assert _map_status("budget_exceeded") == "failed"
        assert _map_status("awaiting_approval") == "input-required"

    def test_unknown_status(self):
        from sandcastle.api.a2a import _map_status
        assert _map_status("some_random_status") == "unknown"


# ===================================================================
# A2A: _extract_workflow_name edge cases
# ===================================================================


class TestA2AExtractWorkflowName:
    def test_non_dict_message(self):
        from sandcastle.api.a2a import _extract_workflow_name
        assert _extract_workflow_name("string") is None
        assert _extract_workflow_name(42) is None
        assert _extract_workflow_name(None) is None

    def test_non_list_parts(self):
        from sandcastle.api.a2a import _extract_workflow_name
        assert _extract_workflow_name({"parts": "not-a-list"}) is None

    def test_non_dict_part(self):
        from sandcastle.api.a2a import _extract_workflow_name
        assert _extract_workflow_name({"parts": ["string-part"]}) is None

    def test_data_part_workflow_name(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "data", "data": {"workflow_name": "my-wf"}}]}
        assert _extract_workflow_name(msg) == "my-wf"

    def test_text_part_workflow_name(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "text", "text": "  my-wf  "}]}
        assert _extract_workflow_name(msg) == "my-wf"

    def test_empty_text_skipped(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "text", "text": "   "}]}
        assert _extract_workflow_name(msg) is None

    def test_data_part_non_string_name(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "data", "data": {"workflow_name": 42}}]}
        assert _extract_workflow_name(msg) is None

    def test_data_part_non_dict_data(self):
        from sandcastle.api.a2a import _extract_workflow_name
        msg = {"parts": [{"type": "data", "data": "not-a-dict"}]}
        assert _extract_workflow_name(msg) is None


# ===================================================================
# A2A: _extract_input edge cases
# ===================================================================


class TestA2AExtractInput:
    def test_non_dict_message(self):
        from sandcastle.api.a2a import _extract_input
        assert _extract_input("string") == {}
        assert _extract_input(None) == {}

    def test_non_list_parts(self):
        from sandcastle.api.a2a import _extract_input
        assert _extract_input({"parts": "not-a-list"}) == {}

    def test_data_part_extracts_input(self):
        from sandcastle.api.a2a import _extract_input
        msg = {"parts": [{"type": "data", "data": {"input": {"key": "val"}}}]}
        assert _extract_input(msg) == {"key": "val"}

    def test_data_part_non_dict_input(self):
        from sandcastle.api.a2a import _extract_input
        msg = {"parts": [{"type": "data", "data": {"input": "not-dict"}}]}
        assert _extract_input(msg) == {}


# ===================================================================
# A2A: _build_task_response
# ===================================================================


class TestA2ABuildTaskResponse:
    def test_basic_task(self):
        from sandcastle.api.a2a import _build_task_response
        task = _build_task_response("t1", "completed")
        assert task["id"] == "t1"
        assert task["status"]["state"] == "completed"
        assert "timestamp" in task["status"]
        assert "artifacts" not in task

    def test_task_with_output(self):
        from sandcastle.api.a2a import _build_task_response
        task = _build_task_response("t1", "completed", output_data={"result": 42})
        assert "artifacts" in task
        assert task["artifacts"][0]["parts"][0]["data"] == {"result": 42}

    def test_task_with_error(self):
        from sandcastle.api.a2a import _build_task_response
        task = _build_task_response("t1", "failed", error="Something broke")
        assert task["status"]["message"] == "Something broke"


# ===================================================================
# A2A: Input validation
# ===================================================================


class TestA2AInputValidation:
    """Test input validation in A2A tasks/send."""

    @pytest.fixture(autouse=True)
    def _reset_rate_limiter(self):
        from sandcastle.api.rate_limit import InMemoryBackend, execution_limiter
        if isinstance(execution_limiter._backend, InMemoryBackend):
            execution_limiter._backend._windows.clear()

    def test_control_chars_in_workflow_name(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {
                "id": str(uuid.uuid4()),
                "message": {
                    "parts": [{"type": "text", "text": "workflow\x00name"}]
                },
            },
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid characters" in result["status"]["message"]

    def test_workflow_name_too_long(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {
                "id": str(uuid.uuid4()),
                "message": {
                    "parts": [{"type": "text", "text": "x" * 256}]
                },
            },
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "too long" in result["status"]["message"].lower()

    def test_invalid_skill_id(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {
                "id": str(uuid.uuid4()),
                "skillId": "nonexistent-skill",
                "message": {"parts": []},
            },
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Unknown skill" in result["status"]["message"]

    def test_task_id_too_long(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {
                "id": "x" * 256,
                "message": {"parts": [{"type": "text", "text": "wf"}]},
            },
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid task id" in result["status"]["message"]


# ===================================================================
# A2A: tasks/get validation
# ===================================================================


class TestA2ATasksGetValidation:
    def test_missing_task_id(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/get",
            "params": {},
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing task id" in result["status"]["message"]

    def test_non_uuid_task_id(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/get",
            "params": {"id": "not-a-uuid"},
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid task id" in result["status"]["message"]

    def test_task_id_too_long_get(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/get",
            "params": {"id": "x" * 256},
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid task id" in result["status"]["message"]


# ===================================================================
# A2A: tasks/cancel validation
# ===================================================================


class TestA2ATasksCancelValidation:
    def test_missing_task_id_cancel(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/cancel",
            "params": {},
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Missing task id" in result["status"]["message"]

    def test_non_uuid_task_id_cancel(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/cancel",
            "params": {"id": "not-a-uuid"},
        })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Invalid task id" in result["status"]["message"]

    def test_cancel_non_cancellable_status(self):
        """Cancelling a completed task should fail."""
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)

        run_id = str(uuid.uuid4())
        mock_run = MagicMock()
        mock_run.id = uuid.UUID(run_id)
        mock_run.status = MagicMock(value="completed")
        mock_run.tenant_id = None

        mock_session_ctx = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_run
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.api.a2a.async_session", mock_session_ctx):
            response = tc.post("/a2a", json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/cancel",
                "params": {"id": run_id},
            })
        data = response.json()
        result = data["result"]
        assert result["status"]["state"] == "failed"
        assert "Cannot cancel" in result["status"]["message"]


# ===================================================================
# A2A: Agent card auth field
# ===================================================================


class TestA2AAgentCardAuth:
    def test_auth_required_shows_required(self):
        """A2A v1.0: auth_required=True should populate the security list."""
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.auth_required = True
            response = tc.get("/.well-known/agent.json")
        data = response.json()
        # v1.0 expresses required auth via the OpenAPI-style `security`
        # array (non-empty) plus a declared scheme in `securitySchemes`.
        assert data["security"] == [{"apiKey": []}]
        assert "apiKey" in data["securitySchemes"]

    def test_auth_not_required_shows_none(self):
        """A2A v1.0: auth_required=False should leave security empty."""
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.auth_required = False
            response = tc.get("/.well-known/agent.json")
        data = response.json()
        assert data["security"] == []
        assert "apiKey" in data["securitySchemes"]


# ===================================================================
# A2A: JSON-RPC error codes
# ===================================================================


class TestA2AErrorCodes:
    """Verify standard JSON-RPC error codes are used correctly."""

    def test_unknown_method_returns_32601(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.post("/a2a", json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/unknown",
            "params": {},
        })
        data = response.json()
        assert data["error"]["code"] == -32601
        assert "Method not found" in data["error"]["message"]

    def test_internal_error_returns_32603(self):
        """Handler exception should map to -32603."""
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        from sandcastle.api import a2a as a2a_module
        tc = TestClient(app, raise_server_exceptions=False)

        async def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        original = a2a_module._METHOD_HANDLERS["tasks/get"]
        a2a_module._METHOD_HANDLERS["tasks/get"] = _boom
        try:
            response = tc.post("/a2a", json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/get",
                "params": {"id": str(uuid.uuid4())},
            })
        finally:
            a2a_module._METHOD_HANDLERS["tasks/get"] = original
        data = response.json()
        assert data["error"]["code"] == -32603
        assert data["error"]["message"] == "Internal error"


# ===================================================================
# A2A: Body size limit
# ===================================================================


class TestA2ABodySizeLimit:
    def test_oversized_body_rejected(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        # Send body > 512 KB
        big_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/get",
            "params": {"id": str(uuid.uuid4()), "extra": "x" * (600 * 1024)},
        })
        response = tc.post(
            "/a2a",
            content=big_payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32600
        assert "too large" in data["error"]["message"].lower()


# ===================================================================
# AG-UI: _emit_terminal_event
# ===================================================================


class TestAguiEmitTerminalEvent:
    """Test the _emit_terminal_event helper dispatches correctly."""

    def test_failed_emits_run_error(self):
        from sandcastle.api.agui import _emit_terminal_event
        mock_run = MagicMock()
        mock_run.error = "Failure reason"
        mock_run.total_cost_usd = 0.0
        events = list(_emit_terminal_event("r1", "failed", mock_run))
        assert len(events) == 1
        parsed = json.loads(events[0].split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "run_error"
        assert parsed["data"]["error"] == "Failure reason"

    def test_budget_exceeded_emits_run_error(self):
        from sandcastle.api.agui import _emit_terminal_event
        mock_run = MagicMock()
        mock_run.error = "Budget exceeded"
        mock_run.total_cost_usd = 10.0
        events = list(_emit_terminal_event("r1", "budget_exceeded", mock_run))
        assert len(events) == 1
        parsed = json.loads(events[0].split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "run_error"

    def test_awaiting_approval_emits_awaiting_input(self):
        from sandcastle.api.agui import _emit_terminal_event
        mock_run = MagicMock()
        mock_run.error = None
        mock_run.total_cost_usd = 0.0
        events = list(_emit_terminal_event("r1", "awaiting_approval", mock_run))
        assert len(events) == 1
        parsed = json.loads(events[0].split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "run_awaiting_input"
        assert "approval" in parsed["data"]["message"].lower()

    def test_completed_emits_run_finished(self):
        from sandcastle.api.agui import _emit_terminal_event
        mock_run = MagicMock()
        mock_run.error = None
        mock_run.total_cost_usd = 0.50
        events = list(_emit_terminal_event("r1", "completed", mock_run))
        assert len(events) == 1
        parsed = json.loads(events[0].split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "run_finished"
        assert parsed["data"]["total_cost"] == 0.50

    def test_cancelled_emits_run_finished(self):
        from sandcastle.api.agui import _emit_terminal_event
        mock_run = MagicMock()
        mock_run.error = None
        mock_run.total_cost_usd = 0.0
        events = list(_emit_terminal_event("r1", "cancelled", mock_run))
        assert len(events) == 1
        parsed = json.loads(events[0].split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "run_finished"

    def test_failed_with_no_error_message(self):
        from sandcastle.api.agui import _emit_terminal_event
        mock_run = MagicMock()
        mock_run.error = None
        mock_run.total_cost_usd = 0.0
        events = list(_emit_terminal_event("r1", "failed", mock_run))
        parsed = json.loads(events[0].split("data: ")[1].split("\n")[0])
        assert parsed["data"]["error"] == "Run failed"


# ===================================================================
# AG-UI: _agui_event format
# ===================================================================


class TestAguiEventFormat:
    def test_event_has_timestamp(self):
        from sandcastle.api.agui import _agui_event
        result = _agui_event("test_type", {"key": "val"})
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["type"] == "test_type"
        assert "timestamp" in parsed
        assert parsed["data"] == {"key": "val"}


# ===================================================================
# AG-UI: _map_event_bus_event edge cases
# ===================================================================


class TestAguiMapEventBusEvent:
    def test_step_failed(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "step.failed",
            "data": {"run_id": "r1", "step_id": "s1"},
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        parsed = json.loads(result.split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "state_delta"
        assert parsed["data"]["value"] == "failed"
        assert "/steps/s1/status" in parsed["data"]["path"]

    def test_step_completed_with_dict_output(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "step.completed",
            "data": {"run_id": "r1", "step_id": "s1", "output": {"result": 42}},
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        lines = [l for l in result.split("\n") if l.startswith("data: ")]
        assert len(lines) == 2
        text_ev = json.loads(lines[0].removeprefix("data: "))
        assert text_ev["type"] == "text_message"
        # Dict output should be JSON-serialized
        content = text_ev["data"]["content"]
        assert json.loads(content) == {"result": 42}

    def test_step_completed_with_string_output(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "step.completed",
            "data": {"run_id": "r1", "step_id": "s1", "output": "Hello"},
        }
        result = _map_event_bus_event(event, "r1")
        lines = [l for l in result.split("\n") if l.startswith("data: ")]
        text_ev = json.loads(lines[0].removeprefix("data: "))
        assert text_ev["data"]["content"] == "Hello"

    def test_run_completed(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "run.completed",
            "data": {"run_id": "r1", "total_cost_usd": 0.15},
        }
        result = _map_event_bus_event(event, "r1")
        assert result is not None
        parsed = json.loads(result.split("data: ")[1].split("\n")[0])
        assert parsed["type"] == "run_finished"
        assert parsed["data"]["total_cost"] == 0.15

    def test_event_without_run_id_not_filtered(self):
        """Events with no run_id should pass through (not be filtered)."""
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "run.started",
            "data": {"workflow_name": "test"},
        }
        result = _map_event_bus_event(event, "r1")
        # run_id is empty string, which is falsy, so it passes the filter
        assert result is not None

    def test_step_started_uses_step_name_fallback(self):
        from sandcastle.api.agui import _map_event_bus_event
        event = {
            "type": "step.started",
            "data": {"run_id": "r1", "step_name": "analyze", "step_type": "code"},
        }
        result = _map_event_bus_event(event, "r1")
        parsed = json.loads(result.split("data: ")[1].split("\n")[0])
        assert parsed["data"]["step_id"] == "analyze"
        assert parsed["data"]["type"] == "code"


# ===================================================================
# AG-UI: Stream for running run
# ===================================================================


class TestAguiStreamRunning:
    """Test that a running run emits run_started on connect."""

    def test_running_run_emits_started(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)

        run_id = str(uuid.uuid4())
        # First call returns running, second call (in event loop timeout poll) returns completed
        mock_run_running = MagicMock()
        mock_run_running.id = uuid.UUID(run_id)
        mock_run_running.workflow_name = "test-wf"
        mock_run_running.status = MagicMock(value="running")
        mock_run_running.total_cost_usd = 0.0
        mock_run_running.error = None

        mock_run_completed = MagicMock()
        mock_run_completed.id = uuid.UUID(run_id)
        mock_run_completed.workflow_name = "test-wf"
        mock_run_completed.status = MagicMock(value="completed")
        mock_run_completed.total_cost_usd = 0.25
        mock_run_completed.error = None

        call_count = [0]

        def make_mock_session():
            mock_session = AsyncMock()
            mock_result = MagicMock()

            def get_run(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] <= 2:
                    mock_result.scalar_one_or_none.return_value = mock_run_running
                else:
                    mock_result.scalar_one_or_none.return_value = mock_run_completed
                return mock_result

            mock_session.execute = AsyncMock(side_effect=get_run)
            return mock_session

        mock_session_ctx = MagicMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=make_mock_session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_queue = asyncio.Queue()

        async def mock_subscribe():
            return mock_queue

        async def mock_unsubscribe(q):
            pass

        with patch("sandcastle.api.agui.async_session", mock_session_ctx):
            with patch("sandcastle.api.agui.event_bus") as mock_bus:
                mock_bus.subscribe = mock_subscribe
                mock_bus.unsubscribe = mock_unsubscribe
                response = tc.get(f"/api/agui/stream/{run_id}")

        assert response.status_code == 200
        # Parse SSE events
        events = []
        for line in response.text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))

        # Should have at least run_started and run_finished
        types = [e["type"] for e in events]
        assert "run_started" in types


# ===================================================================
# AG-UI: _run_status_str
# ===================================================================


class TestAguiRunStatusStr:
    def test_enum_status(self):
        from sandcastle.api.agui import _run_status_str
        mock_run = MagicMock()
        mock_run.status = MagicMock(value="completed")
        assert _run_status_str(mock_run) == "completed"

    def test_string_status(self):
        from sandcastle.api.agui import _run_status_str
        mock_run = MagicMock(spec=[])
        mock_run.status = "running"
        assert _run_status_str(mock_run) == "running"


# ===================================================================
# AG-UI: Invalid run_id format
# ===================================================================


class TestAguiInvalidRunId:
    def test_non_uuid_run_id(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        response = tc.get("/api/agui/stream/not-a-uuid")
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"]["code"] == "INVALID_INPUT"


# ===================================================================
# SDK: Sync + Async run_yaml
# ===================================================================


class TestRunYaml:
    def test_sync_run_yaml(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"run_id": "r1", "status": "completed"}},
            text='{"data": {"run_id": "r1", "status": "completed"}}',
        )
        with patch.object(c._client, "post", return_value=resp):
            result = c.run_yaml("name: test\nsteps: []")
        assert result.run_id == "r1"
        assert result.status == "completed"
        c.close()

    def test_sync_run_yaml_with_options(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"run_id": "r2", "status": "running"}},
            text='{"data": {"run_id": "r2", "status": "running"}}',
        )
        completed_resp = _mock_response(
            json_data={"data": {"run_id": "r2", "status": "completed"}},
            text='{"data": {"run_id": "r2", "status": "completed"}}',
        )
        with patch.object(c._client, "post", return_value=resp):
            with patch.object(c._client, "get", return_value=completed_resp):
                with patch("sandcastle.sdk.time.sleep"):
                    result = c.run_yaml(
                        "name: test\nsteps: []",
                        input={"k": "v"},
                        max_cost_usd=1.0,
                        callback_url="http://example.com/cb",
                        wait=True,
                        poll_interval=0.01,
                    )
        assert result.status == "completed"
        c.close()


# ===================================================================
# SDK: Schedule CRUD
# ===================================================================


class TestScheduleCrud:
    def test_create_schedule(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"id": "sc1", "workflow_name": "wf", "cron_expression": "0 * * * *"}},
            text='{"data": {"id": "sc1", "workflow_name": "wf", "cron_expression": "0 * * * *"}}',
        )
        with patch.object(c._client, "post", return_value=resp):
            result = c.create_schedule("wf", "0 * * * *", input={"k": "v"})
        assert isinstance(result, Schedule)
        assert result.id == "sc1"
        c.close()

    def test_update_schedule(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"id": "sc1", "workflow_name": "wf", "cron_expression": "0 9 * * *", "enabled": False}},
            text='{"data": {"id": "sc1", "workflow_name": "wf", "cron_expression": "0 9 * * *", "enabled": false}}',
        )
        with patch.object(c._client, "patch", return_value=resp):
            result = c.update_schedule("sc1", enabled=False, cron="0 9 * * *")
        assert result.enabled is False
        c.close()

    def test_delete_schedule(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"deleted": True, "id": "sc1"}},
            text='{"data": {"deleted": true, "id": "sc1"}}',
        )
        with patch.object(c._client, "delete", return_value=resp):
            result = c.delete_schedule("sc1")
        assert result["deleted"] is True
        c.close()


# ===================================================================
# SDK: Health, runtime, stats
# ===================================================================


class TestInfoEndpoints:
    def test_health(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"status": "ok", "runtime": True, "database": True, "redis": True}},
            text='{"data": {"status": "ok", "runtime": true, "database": true, "redis": true}}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.health()
        assert isinstance(result, HealthStatus)
        assert result.status == "ok"
        assert result.redis is True
        c.close()

    def test_runtime(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {
                "mode": "local", "database": "sqlite", "queue": "memory",
                "storage": "local", "sandbox_backend": "e2b",
                "license": {"tier": "pro"},
            }},
            text='{"data": {"mode": "local"}}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.runtime()
        assert isinstance(result, RuntimeInfo)
        assert result.sandbox_backend == "e2b"
        assert result.license == {"tier": "pro"}
        c.close()

    def test_stats(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {
                "total_runs_today": 42, "success_rate": 0.95,
                "total_cost_today": 1.5, "avg_duration_seconds": 30.0,
                "runs_by_day": [{"day": "Mon", "count": 10}],
                "cost_by_workflow": [{"wf": "test", "cost": 0.5}],
            }},
            text='{"data": {"total_runs_today": 42}}',
        )
        with patch.object(c._client, "get", return_value=resp):
            result = c.stats()
        assert isinstance(result, Stats)
        assert result.total_runs_today == 42
        assert result.success_rate == 0.95
        c.close()


# ===================================================================
# SDK: Approve / Reject
# ===================================================================


class TestApproveReject:
    def test_approve_with_data_and_comment(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"approved": True, "approval_id": "a1", "run_id": "r1"}},
            text='{"data": {"approved": true}}',
        )
        with patch.object(c._client, "post", return_value=resp) as mock_post:
            result = c.approve("a1", output_data={"result": 42}, comment="LGTM")
        assert result["approved"] is True
        # Verify the body was sent correctly
        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert body["edited_data"] == {"result": 42}
        assert body["comment"] == "LGTM"
        c.close()

    def test_reject_with_reason(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"rejected": True, "approval_id": "a1", "run_id": "r1"}},
            text='{"data": {"rejected": true}}',
        )
        with patch.object(c._client, "post", return_value=resp) as mock_post:
            result = c.reject("a1", reason="Not ready")
        assert result["rejected"] is True
        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert body["comment"] == "Not ready"
        c.close()

    def test_approve_no_body(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"approved": True}},
            text='{"data": {"approved": true}}',
        )
        with patch.object(c._client, "post", return_value=resp) as mock_post:
            c.approve("a1")
        call_args = mock_post.call_args
        # When no body params given, json should be None
        assert call_args[1]["json"] is None
        c.close()


# ===================================================================
# SDK: save_workflow
# ===================================================================


class TestSaveWorkflow:
    def test_save_workflow(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"name": "wf", "description": "d", "steps_count": 3, "file_name": "wf.yaml"}},
            text='{"data": {"name": "wf", "description": "d", "steps_count": 3, "file_name": "wf.yaml"}}',
        )
        with patch.object(c._client, "post", return_value=resp):
            result = c.save_workflow("wf", "name: wf\nsteps: []")
        assert isinstance(result, Workflow)
        assert result.name == "wf"
        assert result.steps_count == 3
        c.close()


# ===================================================================
# SDK: replay / fork
# ===================================================================


class TestReplayFork:
    def test_replay(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"run_id": "new-r1", "status": "running", "replay_from_step": "s2"}},
            text='{"data": {"run_id": "new-r1", "status": "running"}}',
        )
        with patch.object(c._client, "post", return_value=resp):
            result = c.replay("r1", "s2")
        assert result.run_id == "new-r1"
        c.close()

    def test_fork_with_changes(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"run_id": "fork-r1", "status": "running"}},
            text='{"data": {"run_id": "fork-r1", "status": "running"}}',
        )
        with patch.object(c._client, "post", return_value=resp) as mock_post:
            result = c.fork("r1", "s2", changes={"model": "opus"})
        assert result.run_id == "fork-r1"
        body = mock_post.call_args[1]["json"]
        assert body["from_step"] == "s2"
        assert body["changes"] == {"model": "opus"}
        c.close()

    def test_fork_without_changes(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"run_id": "fork-r2", "status": "running"}},
            text='{"data": {"run_id": "fork-r2", "status": "running"}}',
        )
        with patch.object(c._client, "post", return_value=resp) as mock_post:
            c.fork("r1", "s3")
        body = mock_post.call_args[1]["json"]
        assert "changes" not in body
        c.close()


# ===================================================================
# SDK: cancel_run
# ===================================================================


class TestCancelRun:
    def test_cancel_run(self):
        c = SandcastleClient()
        resp = _mock_response(
            json_data={"data": {"cancelled": True, "run_id": "r1"}},
            text='{"data": {"cancelled": true, "run_id": "r1"}}',
        )
        with patch.object(c._client, "post", return_value=resp):
            result = c.cancel_run("r1")
        assert result["cancelled"] is True
        c.close()
