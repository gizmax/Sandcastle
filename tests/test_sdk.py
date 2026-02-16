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
    SandcastleClient,
    SandcastleError,
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
                    "sandstorm": True,
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
        assert result.sandstorm is True
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
            result = client.run(
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
                    "sandstorm": False,
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
        assert result.sandstorm is False
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
