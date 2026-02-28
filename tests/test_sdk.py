"""Tests for the Sandcastle Python SDK (SandcastleClient and AsyncSandcastleClient)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.sdk import (
    AsyncSandcastleClient,
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
    _parse_datetime,
    _parse_run,
    _parse_sse_lines,
    _parse_step,
    _validate_pagination,
    _validate_path_param,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# Tests: SandcastleClient.health()
# ---------------------------------------------------------------------------


class TestSyncHealth:
    def test_health_returns_health_status(self):
        """health() should parse the response into a HealthStatus object."""
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "status": "ok",
                    "runtime": True,
                    "database": True,
                    "redis": None,
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.health()
            client.close()

        assert isinstance(result, HealthStatus)
        assert result.status == "ok"
        assert result.runtime is True
        assert result.database is True
        assert result.redis is None


# ---------------------------------------------------------------------------
# Tests: SandcastleClient.run()
# ---------------------------------------------------------------------------


class TestSyncRun:
    def test_run_sends_correct_body(self):
        """run() should POST to /workflows/run with workflow_name and input."""
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "abc-123",
                    "status": "queued",
                    "workflow_name": "test-wf",
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.run("test-wf", input={"key": "val"})
            client.close()

        assert isinstance(result, Run)
        assert result.run_id == "abc-123"
        assert result.status == "queued"

        # Verify the request body
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["workflow_name"] == "test-wf"
        assert body["input"] == {"key": "val"}

    def test_run_with_optional_params(self):
        """run() should include max_cost_usd and callback_url when provided."""
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "def-456",
                    "status": "queued",
                    "workflow_name": "expensive-wf",
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            client.run(
                "expensive-wf",
                max_cost_usd=10.0,
                callback_url="https://example.com/hook",
            )
            client.close()

        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["max_cost_usd"] == 10.0
        assert body["callback_url"] == "https://example.com/hook"


# ---------------------------------------------------------------------------
# Tests: SandcastleClient.list_runs()
# ---------------------------------------------------------------------------


class TestSyncListRuns:
    def test_list_runs_with_pagination(self):
        """list_runs() should return a PaginatedList with RunListItem objects."""
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "run_id": "run-1",
                        "workflow_name": "wf-a",
                        "status": "completed",
                        "total_cost_usd": 0.01,
                    },
                    {
                        "run_id": "run-2",
                        "workflow_name": "wf-b",
                        "status": "failed",
                        "total_cost_usd": 0.005,
                    },
                ],
                "meta": {"total": 42, "limit": 10, "offset": 0},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_runs(limit=10, offset=0)
            client.close()

        assert isinstance(result, PaginatedList)
        assert result.total == 42
        assert result.limit == 10
        assert len(result.items) == 2
        assert result.items[0].run_id == "run-1"
        assert result.items[1].status == "failed"

    def test_list_runs_with_status_filter(self):
        """list_runs() should pass status param to the request."""
        mock_resp = _mock_response(
            json_data={
                "data": [],
                "meta": {"total": 0, "limit": 50, "offset": 0},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
            client = SandcastleClient(base_url="http://test:8080")
            client.list_runs(status="completed")
            client.close()

        call_args = mock_get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert params["status"] == "completed"


# ---------------------------------------------------------------------------
# Tests: SandcastleClient.get_run()
# ---------------------------------------------------------------------------


class TestSyncGetRun:
    def test_get_run_returns_run_with_steps(self):
        """get_run() should parse a full run including step details."""
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "run-xyz",
                    "status": "completed",
                    "workflow_name": "my-wf",
                    "total_cost_usd": 0.05,
                    "steps": [
                        {
                            "step_id": "step1",
                            "status": "completed",
                            "cost_usd": 0.03,
                            "duration_seconds": 1.2,
                            "attempt": 1,
                        },
                        {
                            "step_id": "step2",
                            "status": "completed",
                            "cost_usd": 0.02,
                            "duration_seconds": 0.8,
                            "attempt": 1,
                        },
                    ],
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.get_run("run-xyz")
            client.close()

        assert isinstance(result, Run)
        assert result.run_id == "run-xyz"
        assert result.status == "completed"
        assert len(result.steps) == 2
        assert result.steps[0].step_id == "step1"
        assert result.steps[1].cost_usd == 0.02


# ---------------------------------------------------------------------------
# Tests: SandcastleClient.cancel_run()
# ---------------------------------------------------------------------------


class TestSyncCancelRun:
    def test_cancel_run_returns_dict(self):
        """cancel_run() should POST to /runs/{id}/cancel and return a dict."""
        mock_resp = _mock_response(
            json_data={
                "data": {"cancelled": True, "run_id": "run-to-cancel"},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.cancel_run("run-to-cancel")
            client.close()

        assert result == {"cancelled": True, "run_id": "run-to-cancel"}
        # Verify the correct URL was called
        call_args = mock_post.call_args
        assert "/runs/run-to-cancel/cancel" in str(call_args)


# ---------------------------------------------------------------------------
# Tests: Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_sync_context_manager_closes_client(self):
        """__exit__ should call close() on the underlying httpx client."""
        with patch.object(httpx.Client, "close") as mock_close:
            with SandcastleClient(base_url="http://test:8080"):
                pass
            mock_close.assert_called_once()

    def test_sync_context_manager_returns_self(self):
        """__enter__ should return the SandcastleClient instance."""
        sc = SandcastleClient(base_url="http://test:8080")
        with patch.object(httpx.Client, "close"):
            with sc as ctx:
                assert ctx is sc


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_404_raises_sandcastle_error(self):
        """A 404 response should raise SandcastleError with the correct fields."""
        mock_resp = _mock_response(
            status_code=404,
            json_data={
                "detail": {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Run not found",
                    }
                }
            },
            text='{"detail": {"error": {"code": "NOT_FOUND", "message": "Run not found"}}}',
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.get_run("nonexistent-id")
            client.close()

        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "NOT_FOUND"
        assert "Run not found" in exc_info.value.message

    def test_500_raises_sandcastle_error(self):
        """A 500 response should raise SandcastleError."""
        mock_resp = _mock_response(
            status_code=500,
            json_data={},
            text="Internal Server Error",
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.health()
            client.close()

        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Tests: API key header
# ---------------------------------------------------------------------------


class TestApiKeyHeader:
    def test_api_key_set_in_headers(self):
        """SandcastleClient should send X-API-Key header when api_key is provided."""
        with patch.object(httpx.Client, "__init__", return_value=None) as mock_init:
            SandcastleClient(base_url="http://test:8080", api_key="sk-test-key")

        call_args = mock_init.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
        assert headers.get("X-API-Key") == "sk-test-key"

    def test_no_api_key_no_header(self):
        """SandcastleClient should not send X-API-Key when api_key is None."""
        with patch.object(httpx.Client, "__init__", return_value=None) as mock_init:
            SandcastleClient(base_url="http://test:8080")

        call_args = mock_init.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
        assert "X-API-Key" not in headers


# ---------------------------------------------------------------------------
# Tests: AsyncSandcastleClient.health()
# ---------------------------------------------------------------------------


class TestAsyncHealth:
    @pytest.mark.asyncio
    async def test_async_health_returns_health_status(self):
        """Async health() should parse the response into a HealthStatus object."""
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "status": "ok",
                    "runtime": False,
                    "database": True,
                    "redis": True,
                },
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.health()
            await client.close()

        assert isinstance(result, HealthStatus)
        assert result.status == "ok"
        assert result.runtime is False
        assert result.database is True
        assert result.redis is True

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """AsyncSandcastleClient should work as an async context manager."""
        with patch.object(
            httpx.AsyncClient, "aclose", new_callable=AsyncMock
        ) as mock_close:
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                assert isinstance(client, AsyncSandcastleClient)
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_get_run(self):
        """Async get_run() should parse a Run object."""
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "async-run-1",
                    "status": "completed",
                    "workflow_name": "async-wf",
                    "total_cost_usd": 0.02,
                },
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.get_run("async-run-1")
            await client.close()

        assert isinstance(result, Run)
        assert result.run_id == "async-run-1"
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Tests: _poll_until_done call ordering (check status before sleeping)
# ---------------------------------------------------------------------------


class TestPollUntilDoneOrdering:
    """Verify that _poll_until_done checks status BEFORE sleeping, not after.

    The fix ensures we don't burn poll_interval seconds unnecessarily when
    the run is already in a terminal state on the very first poll.
    """

    def test_sync_poll_checks_status_before_sleep(self):
        """Sync _poll_until_done should return immediately if already done."""
        import time
        from unittest.mock import call

        completed_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "run-done",
                    "status": "completed",
                    "workflow_name": "wf",
                    "total_cost_usd": 0.01,
                },
                "error": None,
            }
        )

        call_order: list[str] = []

        real_sleep = time.sleep

        def tracked_sleep(t: float) -> None:
            call_order.append("sleep")
            # Don't actually sleep in tests

        def tracked_get(*args, **kwargs):
            call_order.append("get_run")
            return completed_resp

        with (
            patch.object(httpx.Client, "get", side_effect=tracked_get),
            patch("sandcastle.sdk.time.sleep", side_effect=tracked_sleep),
        ):
            client = SandcastleClient(base_url="http://test:8080")
            run = client._poll_until_done("run-done", poll_interval=5.0)
            client.close()

        assert run.status == "completed"
        # get_run should be called before sleep (or sleep never called since done)
        assert "get_run" in call_order
        # If already done, sleep should NOT have been called
        assert "sleep" not in call_order, (
            "_poll_until_done slept before checking status - inefficient"
        )

    def test_sync_poll_sleeps_between_polls(self):
        """Sync _poll_until_done should sleep AFTER each non-terminal status check."""
        import time

        responses = [
            _mock_response(
                json_data={"data": {"run_id": "r", "status": "running", "workflow_name": "wf", "total_cost_usd": 0.0}, "error": None}
            ),
            _mock_response(
                json_data={"data": {"run_id": "r", "status": "completed", "workflow_name": "wf", "total_cost_usd": 0.01}, "error": None}
            ),
        ]
        call_order: list[str] = []
        resp_iter = iter(responses)

        def tracked_get(*args, **kwargs):
            call_order.append("get")
            return next(resp_iter)

        def tracked_sleep(t: float) -> None:
            call_order.append("sleep")

        with (
            patch.object(httpx.Client, "get", side_effect=tracked_get),
            patch("sandcastle.sdk.time.sleep", side_effect=tracked_sleep),
        ):
            client = SandcastleClient(base_url="http://test:8080")
            run = client._poll_until_done("r", poll_interval=1.0)
            client.close()

        assert run.status == "completed"
        # Pattern should be: get, sleep, get (done - no sleep)
        assert call_order == ["get", "sleep", "get"], f"Unexpected order: {call_order}"

    def test_sync_poll_raises_timeout_error(self):
        """Sync _poll_until_done should raise TimeoutError when max_wait exceeded."""
        running_resp = _mock_response(
            json_data={"data": {"run_id": "r", "status": "running", "workflow_name": "wf", "total_cost_usd": 0.0}, "error": None}
        )

        with (
            patch.object(httpx.Client, "get", return_value=running_resp),
            patch("sandcastle.sdk.time.sleep"),
            patch("sandcastle.sdk.time.monotonic", side_effect=[
                0.0,   # deadline = 0.0 + max_wait
                100.0, # first check: not past deadline
                200.0, # second check: past deadline -> TimeoutError
            ]),
        ):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(TimeoutError, match="did not reach terminal status"):
                client._poll_until_done("r", poll_interval=1.0, max_wait=10.0)
            client.close()

    @pytest.mark.asyncio
    async def test_async_poll_checks_status_before_sleeping(self):
        """Async _poll_until_done should return immediately if already done."""
        import asyncio

        completed_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "async-done",
                    "status": "completed",
                    "workflow_name": "wf",
                    "total_cost_usd": 0.0,
                },
                "error": None,
            }
        )
        call_order: list[str] = []

        async def tracked_sleep(t: float) -> None:
            call_order.append("sleep")

        async def tracked_get(*args, **kwargs):
            call_order.append("get_run")
            return completed_resp

        with (
            patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=tracked_get),
            patch("asyncio.sleep", side_effect=tracked_sleep),
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            run = await client._poll_until_done("async-done", poll_interval=5.0)
            await client.close()

        assert run.status == "completed"
        assert "get_run" in call_order
        assert "sleep" not in call_order, (
            "Async _poll_until_done slept before checking status"
        )


# ---------------------------------------------------------------------------
# Tests: _validate_path_param - path traversal / empty strings
# ---------------------------------------------------------------------------


class TestValidatePathParam:
    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_path_param("", "run_id")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_path_param("   ", "run_id")

    def test_slash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_path_param("../etc/passwd", "run_id")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_path_param("..\\etc\\passwd", "run_id")

    def test_none_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_path_param(None, "run_id")  # type: ignore[arg-type]

    def test_integer_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_path_param(123, "run_id")  # type: ignore[arg-type]

    def test_valid_uuid_passes(self):
        result = _validate_path_param("abc-123-def", "run_id")
        assert result == "abc-123-def"


class TestValidatePagination:
    def test_negative_limit_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            _validate_pagination(-1, 0)

    def test_zero_limit_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            _validate_pagination(0, 0)

    def test_limit_over_200_rejected(self):
        with pytest.raises(ValueError, match="must not exceed 200"):
            _validate_pagination(201, 0)

    def test_negative_offset_rejected(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            _validate_pagination(50, -1)

    def test_valid_pagination_passes(self):
        _validate_pagination(200, 0)
        _validate_pagination(1, 1000)

    def test_float_limit_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            _validate_pagination(50.5, 0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests: Path traversal guards on sync methods
# ---------------------------------------------------------------------------


class TestSyncPathTraversal:
    def test_get_run_rejects_slash(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            client.get_run("../admin/secret")
        client.close()

    def test_cancel_run_rejects_empty(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="non-empty"):
            client.cancel_run("")
        client.close()

    def test_replay_rejects_slash(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            client.replay("../../run", "step1")
        client.close()

    def test_fork_rejects_slash(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            client.fork("a/b", "step1")
        client.close()

    def test_stream_rejects_slash(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            # stream is a generator, must call next() or iterate to trigger
            gen = client.stream("a/b")
            next(gen)
        client.close()

    def test_update_schedule_rejects_empty(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="non-empty"):
            client.update_schedule("  ")
        client.close()

    def test_delete_schedule_rejects_slash(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            client.delete_schedule("../secret")
        client.close()


# ---------------------------------------------------------------------------
# Tests: Path traversal guards on async methods
# ---------------------------------------------------------------------------


class TestAsyncPathTraversal:
    @pytest.mark.asyncio
    async def test_async_get_run_rejects_slash(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            await client.get_run("../admin")
        await client.close()

    @pytest.mark.asyncio
    async def test_async_cancel_run_rejects_empty(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="non-empty"):
            await client.cancel_run("")
        await client.close()

    @pytest.mark.asyncio
    async def test_async_replay_rejects_slash(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            await client.replay("a/b", "step1")
        await client.close()

    @pytest.mark.asyncio
    async def test_async_fork_rejects_slash(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            await client.fork("a/b", "step1")
        await client.close()

    @pytest.mark.asyncio
    async def test_async_update_schedule_rejects_slash(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            await client.update_schedule("a/b")
        await client.close()

    @pytest.mark.asyncio
    async def test_async_delete_schedule_rejects_empty(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="non-empty"):
            await client.delete_schedule("   ")
        await client.close()


# ---------------------------------------------------------------------------
# Tests: Pagination validation on list methods
# ---------------------------------------------------------------------------


class TestPaginationValidation:
    def test_list_runs_rejects_negative_limit(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="positive integer"):
            client.list_runs(limit=-5)
        client.close()

    def test_list_runs_rejects_limit_over_200(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="must not exceed 200"):
            client.list_runs(limit=500)
        client.close()

    def test_list_runs_rejects_negative_offset(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="non-negative"):
            client.list_runs(offset=-1)
        client.close()

    def test_list_schedules_rejects_zero_limit(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="positive integer"):
            client.list_schedules(limit=0)
        client.close()

    @pytest.mark.asyncio
    async def test_async_list_runs_rejects_negative_limit(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="positive integer"):
            await client.list_runs(limit=-1)
        await client.close()

    @pytest.mark.asyncio
    async def test_async_list_schedules_rejects_over_200(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="must not exceed 200"):
            await client.list_schedules(limit=201)
        await client.close()


# ---------------------------------------------------------------------------
# Tests: _extract_data - malformed JSON on success response
# ---------------------------------------------------------------------------


class TestExtractData:
    def test_non_json_success_raises_sandcastle_error(self):
        """A 200 response with non-JSON body should raise SandcastleError."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "<html>Gateway timeout</html>"
        resp.json.side_effect = Exception("Not JSON")

        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)

        assert exc_info.value.status_code == 200
        assert exc_info.value.code == "INVALID_RESPONSE"
        assert "Gateway timeout" in exc_info.value.message

    def test_error_with_non_json_body_uses_text(self):
        """A 502 response with HTML body should use truncated text."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 502
        resp.text = "<html>Bad Gateway</html>"
        resp.json.side_effect = Exception("Not JSON")

        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)

        assert exc_info.value.status_code == 502
        assert exc_info.value.code == "API_ERROR"
        assert "Bad Gateway" in exc_info.value.message

    def test_error_with_empty_text(self):
        """A 500 response with empty body should use fallback message."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 500
        resp.text = ""
        resp.json.side_effect = Exception("Not JSON")

        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)

        assert exc_info.value.message == "Unknown API error"

    def test_success_with_data_key(self):
        """A 200 response with 'data' key should extract it."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {"data": {"foo": "bar"}, "error": None}

        result = _extract_data(resp)
        assert result == {"foo": "bar"}

    def test_success_without_data_key_returns_full_body(self):
        """A 200 response without 'data' key should return full body."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {"foo": "bar"}

        result = _extract_data(resp)
        assert result == {"foo": "bar"}

    def test_very_long_error_text_truncated(self):
        """Error message from response.text should be truncated to 500 chars."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 500
        resp.text = "x" * 1000
        resp.json.side_effect = Exception("Not JSON")

        with pytest.raises(SandcastleError) as exc_info:
            _extract_data(resp)

        assert len(exc_info.value.message) == 500


# ---------------------------------------------------------------------------
# Tests: _parse_datetime edge cases
# ---------------------------------------------------------------------------


class TestParseDatetime:
    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self):
        from datetime import datetime
        dt = datetime(2024, 1, 1)
        assert _parse_datetime(dt) is dt

    def test_valid_iso_string(self):
        from datetime import datetime
        result = _parse_datetime("2024-01-15T10:30:00")
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_invalid_string_returns_none(self):
        assert _parse_datetime("not-a-date") is None

    def test_integer_coerced_to_string(self):
        # str(12345) is "12345" which is not valid ISO
        assert _parse_datetime(12345) is None

    def test_empty_string_returns_none(self):
        assert _parse_datetime("") is None


# ---------------------------------------------------------------------------
# Tests: _parse_step edge cases
# ---------------------------------------------------------------------------


class TestParseStep:
    def test_empty_dict_uses_defaults(self):
        step = _parse_step({})
        assert step.step_id == ""
        assert step.status == "unknown"
        assert step.cost_usd == 0.0
        assert step.attempt == 1
        assert step.error is None

    def test_all_fields_parsed(self):
        step = _parse_step({
            "step_id": "s1",
            "status": "completed",
            "parallel_index": 2,
            "output": {"result": "ok"},
            "cost_usd": 0.05,
            "duration_seconds": 3.14,
            "attempt": 3,
            "error": "something broke",
        })
        assert step.step_id == "s1"
        assert step.parallel_index == 2
        assert step.output == {"result": "ok"}
        assert step.attempt == 3
        assert step.error == "something broke"


# ---------------------------------------------------------------------------
# Tests: _parse_run edge cases
# ---------------------------------------------------------------------------


class TestParseRun:
    def test_empty_dict_uses_defaults(self):
        run = _parse_run({})
        assert run.run_id == ""
        assert run.status == "unknown"
        assert run.steps is None
        assert run.depth == 0

    def test_new_run_id_fallback(self):
        """If run_id is missing, new_run_id should be used."""
        run = _parse_run({"new_run_id": "fork-123", "status": "queued"})
        assert run.run_id == "fork-123"
        assert run.new_run_id == "fork-123"

    def test_steps_parsed(self):
        run = _parse_run({
            "run_id": "r1",
            "status": "completed",
            "steps": [{"step_id": "s1", "status": "done"}],
        })
        assert len(run.steps) == 1
        assert run.steps[0].step_id == "s1"

    def test_none_steps_stays_none(self):
        run = _parse_run({"run_id": "r1", "status": "running", "steps": None})
        assert run.steps is None


# ---------------------------------------------------------------------------
# Tests: _parse_sse_lines
# ---------------------------------------------------------------------------


class TestParseSSELines:
    def test_single_event(self):
        raw = "event:status\ndata:{\"status\":\"running\"}\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["_event"] == "status"
        assert events[0]["status"] == "running"

    def test_multiple_events(self):
        raw = (
            "event:step\ndata:{\"step_id\":\"s1\"}\n\n"
            "event:result\ndata:{\"ok\":true}\n\n"
        )
        events = list(_parse_sse_lines(raw))
        assert len(events) == 2
        assert events[0]["_event"] == "step"
        assert events[1]["_event"] == "result"

    def test_multi_line_data(self):
        raw = "event:big\ndata:{\"a\": 1,\ndata: \"b\": 2}\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["a"] == 1

    def test_non_json_data_uses_raw(self):
        raw = "data:not json\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["raw"] == "not json"

    def test_flush_without_trailing_blank(self):
        raw = "event:end\ndata:{\"done\":true}"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert events[0]["done"] is True

    def test_empty_string_yields_nothing(self):
        events = list(_parse_sse_lines(""))
        assert events == []


# ---------------------------------------------------------------------------
# Tests: __repr__ - API key redaction
# ---------------------------------------------------------------------------


class TestRepr:
    def test_sync_repr_with_key_redacted(self):
        client = SandcastleClient(base_url="http://test:8080", api_key="sk-secret")
        r = repr(client)
        assert "sk-secret" not in r
        assert "api_key=***" in r
        assert "http://test:8080" in r
        client.close()

    def test_sync_repr_without_key(self):
        client = SandcastleClient(base_url="http://test:8080")
        r = repr(client)
        assert "api_key=None" in r
        client.close()

    def test_async_repr_with_key_redacted(self):
        client = AsyncSandcastleClient(base_url="http://test:8080", api_key="sk-secret")
        r = repr(client)
        assert "sk-secret" not in r
        assert "api_key=***" in r
        assert "AsyncSandcastleClient" in r

    def test_async_repr_without_key(self):
        client = AsyncSandcastleClient(base_url="http://test:8080")
        r = repr(client)
        assert "api_key=None" in r


# ---------------------------------------------------------------------------
# Tests: Untested sync methods - run_yaml, replay, fork, save_workflow,
#        list_workflows, create_schedule, update_schedule, delete_schedule,
#        runtime, stats
# ---------------------------------------------------------------------------


class TestSyncRunYaml:
    def test_run_yaml_sends_yaml_content(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "yaml-1", "status": "queued", "workflow_name": ""},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.run_yaml("name: test\nsteps: []")
            client.close()

        assert result.run_id == "yaml-1"
        body = mock_post.call_args.kwargs.get("json")
        assert body["workflow"] == "name: test\nsteps: []"

    def test_run_yaml_with_input_and_cost(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "yaml-2", "status": "queued", "workflow_name": ""},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            client.run_yaml(
                "name: test\nsteps: []",
                input={"x": 1},
                max_cost_usd=5.0,
                callback_url="https://cb.example.com",
            )
            client.close()

        body = mock_post.call_args.kwargs.get("json")
        assert body["input"] == {"x": 1}
        assert body["max_cost_usd"] == 5.0
        assert body["callback_url"] == "https://cb.example.com"


class TestSyncReplay:
    def test_replay_returns_new_run(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "replay-1",
                    "new_run_id": "replay-1",
                    "status": "queued",
                    "workflow_name": "wf",
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.replay("original-run", "step-3")
            client.close()

        assert result.run_id == "replay-1"
        body = mock_post.call_args.kwargs.get("json")
        assert body["from_step"] == "step-3"


class TestSyncFork:
    def test_fork_with_changes(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "run_id": "fork-1",
                    "status": "queued",
                    "workflow_name": "wf",
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.fork("orig-run", "step-2", changes={"model": "opus"})
            client.close()

        assert result.run_id == "fork-1"
        body = mock_post.call_args.kwargs.get("json")
        assert body["from_step"] == "step-2"
        assert body["changes"] == {"model": "opus"}

    def test_fork_without_changes(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "fork-2", "status": "queued", "workflow_name": "wf"},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            client.fork("orig-run", "step-1")
            client.close()

        body = mock_post.call_args.kwargs.get("json")
        assert "changes" not in body


class TestSyncListWorkflows:
    def test_list_workflows_returns_list(self):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {"name": "wf1", "description": "desc1", "steps_count": 3, "file_name": "wf1.yaml"},
                    {"name": "wf2", "description": "desc2", "steps_count": 1, "file_name": "wf2.yaml"},
                ],
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_workflows()
            client.close()

        assert len(result) == 2
        assert isinstance(result[0], Workflow)
        assert result[0].name == "wf1"
        assert result[1].steps_count == 1

    def test_list_workflows_empty(self):
        mock_resp = _mock_response(json_data={"data": [], "error": None})

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_workflows()
            client.close()

        assert result == []

    def test_list_workflows_non_list_returns_empty(self):
        """If data is not a list (unexpected), return []."""
        mock_resp = _mock_response(
            json_data={"data": {"unexpected": True}, "error": None}
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_workflows()
            client.close()

        assert result == []


class TestSyncSaveWorkflow:
    def test_save_workflow(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "name": "new-wf",
                    "description": "",
                    "steps_count": 2,
                    "file_name": "new-wf.yaml",
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.save_workflow("new-wf", "name: new-wf\nsteps: []")
            client.close()

        assert isinstance(result, Workflow)
        assert result.name == "new-wf"
        body = mock_post.call_args.kwargs.get("json")
        assert body["name"] == "new-wf"
        assert body["content"] == "name: new-wf\nsteps: []"


class TestSyncSchedules:
    def test_list_schedules(self):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {"id": "s1", "workflow_name": "wf", "cron_expression": "0 9 * * *"},
                ],
                "meta": {"total": 1, "limit": 50, "offset": 0},
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_schedules()
            client.close()

        assert isinstance(result, PaginatedList)
        assert len(result.items) == 1
        assert isinstance(result.items[0], Schedule)
        assert result.items[0].cron_expression == "0 9 * * *"

    def test_create_schedule(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "id": "s-new",
                    "workflow_name": "daily-wf",
                    "cron_expression": "0 8 * * 1-5",
                    "enabled": True,
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.create_schedule(
                "daily-wf", "0 8 * * 1-5", input={"key": "val"}
            )
            client.close()

        assert isinstance(result, Schedule)
        assert result.id == "s-new"
        body = mock_post.call_args.kwargs.get("json")
        assert body["input_data"] == {"key": "val"}

    def test_update_schedule(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "id": "s1",
                    "workflow_name": "wf",
                    "cron_expression": "0 10 * * *",
                    "enabled": False,
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "patch", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.update_schedule("s1", enabled=False, cron="0 10 * * *")
            client.close()

        assert result.enabled is False
        assert result.cron_expression == "0 10 * * *"

    def test_delete_schedule(self):
        mock_resp = _mock_response(
            json_data={"data": {"deleted": True, "id": "s-del"}, "error": None}
        )

        with patch.object(httpx.Client, "delete", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.delete_schedule("s-del")
            client.close()

        assert result == {"deleted": True, "id": "s-del"}


class TestSyncRuntime:
    def test_runtime_returns_runtime_info(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "mode": "development",
                    "database": "sqlite",
                    "queue": "memory",
                    "storage": "local",
                    "sandbox_backend": "e2b",
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.runtime()
            client.close()

        assert isinstance(result, RuntimeInfo)
        assert result.mode == "development"
        assert result.sandbox_backend == "e2b"


class TestSyncStats:
    def test_stats_returns_stats(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "total_runs_today": 42,
                    "success_rate": 0.95,
                    "total_cost_today": 1.23,
                    "avg_duration_seconds": 4.5,
                },
                "error": None,
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.stats()
            client.close()

        assert isinstance(result, Stats)
        assert result.total_runs_today == 42
        assert result.success_rate == 0.95


# ---------------------------------------------------------------------------
# Tests: list_runs / list_schedules with non-JSON response
# ---------------------------------------------------------------------------


class TestListNonJsonResponse:
    def test_list_runs_non_json_raises_error(self):
        """list_runs should raise SandcastleError on non-JSON 200 response."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "<html>Not JSON</html>"
        resp.json.side_effect = Exception("Not JSON")

        with patch.object(httpx.Client, "get", return_value=resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.list_runs()
            client.close()

        assert exc_info.value.code == "INVALID_RESPONSE"

    def test_list_schedules_non_json_raises_error(self):
        """list_schedules should raise SandcastleError on non-JSON 200 response."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "Bad Gateway"
        resp.json.side_effect = Exception("Not JSON")

        with patch.object(httpx.Client, "get", return_value=resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.list_schedules()
            client.close()

        assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_async_list_runs_non_json_raises_error(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "<html>Error</html>"
        resp.json.side_effect = Exception("Not JSON")

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                await client.list_runs()
            await client.close()

        assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_async_list_schedules_non_json_raises_error(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "Timeout"
        resp.json.side_effect = Exception("Not JSON")

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                await client.list_schedules()
            await client.close()

        assert exc_info.value.code == "INVALID_RESPONSE"


# ---------------------------------------------------------------------------
# Tests: Async client methods - run, run_yaml, list_runs, list_workflows,
#        save_workflow, list_schedules, create_schedule, update_schedule,
#        delete_schedule, cancel_run, replay, fork, runtime, stats
# ---------------------------------------------------------------------------


class TestAsyncRunMethods:
    @pytest.mark.asyncio
    async def test_async_run(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "async-r1", "status": "queued", "workflow_name": "wf"},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.run("wf", input={"k": "v"})
            await client.close()

        assert result.run_id == "async-r1"

    @pytest.mark.asyncio
    async def test_async_run_yaml(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "async-y1", "status": "queued", "workflow_name": ""},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.run_yaml("name: t\nsteps: []")
            await client.close()

        assert result.run_id == "async-y1"

    @pytest.mark.asyncio
    async def test_async_cancel_run(self):
        mock_resp = _mock_response(
            json_data={"data": {"cancelled": True, "run_id": "ar1"}, "error": None}
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.cancel_run("ar1")
            await client.close()

        assert result["cancelled"] is True

    @pytest.mark.asyncio
    async def test_async_replay(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "replay-a1", "status": "queued", "workflow_name": "wf"},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.replay("orig", "step-1")
            await client.close()

        assert result.run_id == "replay-a1"

    @pytest.mark.asyncio
    async def test_async_fork(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"run_id": "fork-a1", "status": "queued", "workflow_name": "wf"},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.fork("orig", "step-1", changes={"model": "haiku"})
            await client.close()

        assert result.run_id == "fork-a1"

    @pytest.mark.asyncio
    async def test_async_list_runs(self):
        mock_resp = _mock_response(
            json_data={
                "data": [{"run_id": "r1", "workflow_name": "wf", "status": "done"}],
                "meta": {"total": 1, "limit": 50, "offset": 0},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.list_runs()
            await client.close()

        assert isinstance(result, PaginatedList)
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_async_list_workflows(self):
        mock_resp = _mock_response(
            json_data={
                "data": [{"name": "wf1", "description": "", "steps_count": 1, "file_name": "wf1.yaml"}],
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.list_workflows()
            await client.close()

        assert len(result) == 1
        assert isinstance(result[0], Workflow)

    @pytest.mark.asyncio
    async def test_async_save_workflow(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"name": "wf2", "description": "", "steps_count": 0, "file_name": "wf2.yaml"},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.save_workflow("wf2", "steps: []")
            await client.close()

        assert result.name == "wf2"

    @pytest.mark.asyncio
    async def test_async_create_schedule(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"id": "as1", "workflow_name": "wf", "cron_expression": "* * * * *"},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.create_schedule("wf", "* * * * *")
            await client.close()

        assert isinstance(result, Schedule)

    @pytest.mark.asyncio
    async def test_async_update_schedule(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "id": "as1", "workflow_name": "wf",
                    "cron_expression": "0 0 * * *", "enabled": False,
                },
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "patch", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.update_schedule("as1", enabled=False)
            await client.close()

        assert result.enabled is False

    @pytest.mark.asyncio
    async def test_async_delete_schedule(self):
        mock_resp = _mock_response(
            json_data={"data": {"deleted": True, "id": "as1"}, "error": None}
        )

        with patch.object(
            httpx.AsyncClient, "delete", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.delete_schedule("as1")
            await client.close()

        assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_async_runtime(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"mode": "dev", "database": "sqlite", "queue": "memory", "storage": "local"},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.runtime()
            await client.close()

        assert isinstance(result, RuntimeInfo)
        assert result.mode == "dev"

    @pytest.mark.asyncio
    async def test_async_stats(self):
        mock_resp = _mock_response(
            json_data={
                "data": {"total_runs_today": 10, "success_rate": 1.0},
                "error": None,
            }
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = AsyncSandcastleClient(base_url="http://test:8080")
            result = await client.stats()
            await client.close()

        assert isinstance(result, Stats)
        assert result.total_runs_today == 10


# ---------------------------------------------------------------------------
# Tests: SandcastleError str representation
# ---------------------------------------------------------------------------


class TestSandcastleErrorRepr:
    def test_error_str(self):
        err = SandcastleError(404, "NOT_FOUND", "Run not found")
        assert "[404] NOT_FOUND: Run not found" in str(err)

    def test_error_attributes(self):
        err = SandcastleError(429, "RATE_LIMITED", "Too many requests")
        assert err.status_code == 429
        assert err.code == "RATE_LIMITED"
        assert err.message == "Too many requests"
