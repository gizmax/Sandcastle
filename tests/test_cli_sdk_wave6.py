"""Wave 6 deep audit tests for CLI and SDK remaining issues.

Covers:
1. SDK approve/reject/list_approvals methods (sync + async)
2. SDK stream error handling (connection drops, malformed events)
3. CLI doctor completeness (database/Redis connectivity checks)
4. CLI run error reporting (structured SandcastleError display)
5. SDK Approval dataclass and _parse_approval helper
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.sdk import (
    Approval,
    AsyncSandcastleClient,
    PaginatedList,
    SandcastleClient,
    SandcastleError,
    _parse_approval,
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


@contextmanager
def _capture_stderr():
    """Capture stderr output within a context."""
    old = sys.stderr
    sys.stderr = io.StringIO()
    try:
        yield sys.stderr
    finally:
        sys.stderr = old


# ===========================================================================
# Section 1: SDK Approval dataclass and _parse_approval
# ===========================================================================


class TestParseApproval:
    """Tests for _parse_approval helper and Approval dataclass."""

    def test_empty_dict_uses_defaults(self):
        a = _parse_approval({})
        assert a.id == ""
        assert a.run_id == ""
        assert a.step_id == ""
        assert a.status == "unknown"
        assert a.request_data is None
        assert a.response_data is None
        assert a.message == ""
        assert a.reviewer_id is None
        assert a.reviewer_comment is None
        assert a.timeout_at is None
        assert a.on_timeout == "abort"
        assert a.allow_edit is False
        assert a.created_at is None
        assert a.resolved_at is None

    def test_all_fields_parsed(self):
        data = {
            "id": "appr-123",
            "run_id": "run-456",
            "step_id": "step_1",
            "status": "pending",
            "request_data": {"key": "value"},
            "response_data": {"approved": True},
            "message": "Please review",
            "reviewer_id": "user-789",
            "reviewer_comment": "Looks good",
            "timeout_at": "2026-03-01T12:00:00",
            "on_timeout": "skip",
            "allow_edit": True,
            "created_at": "2026-02-28T10:00:00",
            "resolved_at": "2026-02-28T11:00:00",
        }
        a = _parse_approval(data)
        assert a.id == "appr-123"
        assert a.run_id == "run-456"
        assert a.step_id == "step_1"
        assert a.status == "pending"
        assert a.request_data == {"key": "value"}
        assert a.response_data == {"approved": True}
        assert a.message == "Please review"
        assert a.reviewer_id == "user-789"
        assert a.reviewer_comment == "Looks good"
        assert a.timeout_at is not None
        assert a.on_timeout == "skip"
        assert a.allow_edit is True
        assert a.created_at is not None
        assert a.resolved_at is not None

    def test_invalid_datetime_returns_none(self):
        a = _parse_approval({"timeout_at": "not-a-date", "created_at": 12345})
        assert a.timeout_at is None
        # int coerced to string "12345" which is not a valid ISO date
        assert a.created_at is None


# ===========================================================================
# Section 2: SDK sync approve/reject/list_approvals
# ===========================================================================


class TestSyncApprove:
    """Tests for SandcastleClient.approve()."""

    def test_approve_sends_correct_request(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "approved": True,
                    "approval_id": "appr-123",
                    "run_id": "run-456",
                },
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.approve("appr-123")
            client.close()

        assert result["approved"] is True
        assert result["approval_id"] == "appr-123"
        mock_post.assert_called_once_with(
            "/api/approvals/appr-123/approve",
            json=None,
        )

    def test_approve_with_output_data_and_comment(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "approved": True,
                    "approval_id": "appr-123",
                    "run_id": "run-456",
                },
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.approve(
                "appr-123",
                output_data={"edited": "data"},
                comment="Approved with edits",
            )
            client.close()

        assert result["approved"] is True
        mock_post.assert_called_once_with(
            "/api/approvals/appr-123/approve",
            json={"edited_data": {"edited": "data"}, "comment": "Approved with edits"},
        )

    def test_approve_rejects_empty_id(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="approval_id"):
            client.approve("")
        client.close()

    def test_approve_rejects_path_traversal(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            client.approve("../../etc/passwd")
        client.close()


class TestSyncReject:
    """Tests for SandcastleClient.reject()."""

    def test_reject_sends_correct_request(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "rejected": True,
                    "approval_id": "appr-123",
                    "run_id": "run-456",
                },
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.reject("appr-123")
            client.close()

        assert result["rejected"] is True
        mock_post.assert_called_once_with(
            "/api/approvals/appr-123/reject",
            json=None,
        )

    def test_reject_with_reason(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "rejected": True,
                    "approval_id": "appr-123",
                    "run_id": "run-456",
                },
            }
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.reject("appr-123", reason="Not ready yet")
            client.close()

        assert result["rejected"] is True
        mock_post.assert_called_once_with(
            "/api/approvals/appr-123/reject",
            json={"comment": "Not ready yet"},
        )

    def test_reject_rejects_slash(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            client.reject("foo/bar")
        client.close()


class TestSyncListApprovals:
    """Tests for SandcastleClient.list_approvals()."""

    def test_list_approvals_returns_paginated_list(self):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "id": "appr-1",
                        "run_id": "run-1",
                        "step_id": "step_1",
                        "status": "pending",
                    },
                    {
                        "id": "appr-2",
                        "run_id": "run-2",
                        "step_id": "step_2",
                        "status": "approved",
                    },
                ],
                "meta": {"total": 2, "limit": 50, "offset": 0},
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_approvals()
            client.close()

        assert isinstance(result, PaginatedList)
        assert len(result.items) == 2
        assert result.total == 2
        assert isinstance(result.items[0], Approval)
        assert result.items[0].id == "appr-1"
        assert result.items[0].status == "pending"
        assert result.items[1].id == "appr-2"
        assert result.items[1].status == "approved"

    def test_list_approvals_with_status_filter(self):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "id": "appr-1",
                        "run_id": "run-1",
                        "step_id": "step_1",
                        "status": "pending",
                    },
                ],
                "meta": {"total": 1, "limit": 50, "offset": 0},
            }
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
            client = SandcastleClient(base_url="http://test:8080")
            result = client.list_approvals(status="pending")
            client.close()

        assert len(result.items) == 1
        # Verify the status filter was passed
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["status"] == "pending"

    def test_list_approvals_validates_pagination(self):
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="limit must be a positive"):
            client.list_approvals(limit=0)
        with pytest.raises(ValueError, match="limit must not exceed"):
            client.list_approvals(limit=201)
        with pytest.raises(ValueError, match="offset must be a non-negative"):
            client.list_approvals(offset=-1)
        client.close()

    def test_list_approvals_handles_api_error(self):
        mock_resp = _mock_response(
            status_code=400,
            json_data={
                "detail": {
                    "error": {
                        "code": "INVALID_STATUS",
                        "message": "Invalid status 'bogus'",
                    }
                }
            },
            text='{"detail":{"error":{"code":"INVALID_STATUS","message":"Invalid status"}}}',
        )

        with patch.object(httpx.Client, "get", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.list_approvals(status="bogus")
            client.close()

        assert exc_info.value.status_code == 400


# ===========================================================================
# Section 3: SDK async approve/reject/list_approvals
# ===========================================================================


class TestAsyncApprove:
    @pytest.mark.asyncio
    async def test_async_approve(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "approved": True,
                    "approval_id": "appr-async",
                    "run_id": "run-async",
                },
            }
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                result = await client.approve("appr-async")

        assert result["approved"] is True
        assert result["approval_id"] == "appr-async"

    @pytest.mark.asyncio
    async def test_async_approve_with_data(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "approved": True,
                    "approval_id": "appr-async",
                    "run_id": "run-async",
                },
            }
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp) as mock_post:
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                result = await client.approve(
                    "appr-async",
                    output_data={"changed": True},
                    comment="LGTM",
                )

        mock_post.assert_called_once_with(
            "/api/approvals/appr-async/approve",
            json={"edited_data": {"changed": True}, "comment": "LGTM"},
        )

    @pytest.mark.asyncio
    async def test_async_approve_rejects_empty_id(self):
        async with AsyncSandcastleClient(base_url="http://test:8080") as client:
            with pytest.raises(ValueError, match="approval_id"):
                await client.approve("  ")


class TestAsyncReject:
    @pytest.mark.asyncio
    async def test_async_reject(self):
        mock_resp = _mock_response(
            json_data={
                "data": {
                    "rejected": True,
                    "approval_id": "appr-async",
                    "run_id": "run-async",
                },
            }
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                result = await client.reject("appr-async", reason="Needs rework")

        assert result["rejected"] is True

    @pytest.mark.asyncio
    async def test_async_reject_rejects_slash(self):
        async with AsyncSandcastleClient(base_url="http://test:8080") as client:
            with pytest.raises(ValueError, match="path separators"):
                await client.reject("../bad")


class TestAsyncListApprovals:
    @pytest.mark.asyncio
    async def test_async_list_approvals(self):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "id": "appr-1",
                        "run_id": "run-1",
                        "step_id": "step_1",
                        "status": "pending",
                    },
                ],
                "meta": {"total": 1, "limit": 50, "offset": 0},
            }
        )

        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                result = await client.list_approvals(status="pending")

        assert isinstance(result, PaginatedList)
        assert len(result.items) == 1
        assert isinstance(result.items[0], Approval)

    @pytest.mark.asyncio
    async def test_async_list_approvals_validates_pagination(self):
        async with AsyncSandcastleClient(base_url="http://test:8080") as client:
            with pytest.raises(ValueError, match="limit"):
                await client.list_approvals(limit=-1)


# ===========================================================================
# Section 4: SDK stream error handling
# ===========================================================================


class TestSyncStreamErrorHandling:
    """Tests for SandcastleClient.stream() error handling."""

    def test_stream_connection_error_raises_sandcastle_error(self):
        """ConnectError before stream starts should raise SandcastleError."""
        with patch.object(
            httpx.Client, "stream",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                # Must consume the generator to trigger the error
                list(client.stream("550e8400-e29b-41d4-a716-446655440000"))
            client.close()

        assert exc_info.value.code == "CONNECTION_ERROR"
        assert "Connection refused" in exc_info.value.message

    def test_stream_timeout_raises_sandcastle_error(self):
        """TimeoutException should raise SandcastleError with TIMEOUT_ERROR code."""
        with patch.object(
            httpx.Client, "stream",
            side_effect=httpx.ReadTimeout("Read timed out"),
        ):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                list(client.stream("550e8400-e29b-41d4-a716-446655440000"))
            client.close()

        assert exc_info.value.code == "TIMEOUT_ERROR"

    def test_stream_mid_stream_error_yields_error_event(self):
        """StreamError during iteration should yield a stream_error event."""
        # Create a mock context manager for the stream
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        # Simulate: yield one line, then raise StreamError
        def iter_lines_with_error():
            yield "event: status"
            yield 'data: {"status": "running"}'
            yield ""
            raise httpx.StreamError("Connection reset")

        mock_resp.iter_lines = iter_lines_with_error

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_resp)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(httpx.Client, "stream", return_value=mock_ctx):
            client = SandcastleClient(base_url="http://test:8080")
            events = list(client.stream("550e8400-e29b-41d4-a716-446655440000"))
            client.close()

        # Should have the first event + the error event
        assert len(events) == 2
        assert events[0]["_event"] == "status"
        assert events[0]["status"] == "running"
        assert events[1]["_event"] == "stream_error"
        assert "Connection reset" in events[1]["message"]

    def test_stream_validates_run_id(self):
        """Stream should reject invalid run IDs."""
        client = SandcastleClient(base_url="http://test:8080")
        with pytest.raises(ValueError, match="path separators"):
            list(client.stream("../traversal"))
        client.close()


class TestAsyncStreamErrorHandling:
    """Tests for AsyncSandcastleClient.stream() error handling."""

    @pytest.mark.asyncio
    async def test_async_stream_connection_error(self):
        """ConnectError should raise SandcastleError."""
        with patch.object(
            httpx.AsyncClient, "stream",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                with pytest.raises(SandcastleError) as exc_info:
                    events = []
                    async for event in client.stream("550e8400-e29b-41d4-a716-446655440000"):
                        events.append(event)

        assert exc_info.value.code == "CONNECTION_ERROR"

    @pytest.mark.asyncio
    async def test_async_stream_timeout_error(self):
        """TimeoutException should raise SandcastleError."""
        with patch.object(
            httpx.AsyncClient, "stream",
            side_effect=httpx.ReadTimeout("Timed out"),
        ):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                with pytest.raises(SandcastleError) as exc_info:
                    async for _ in client.stream("550e8400-e29b-41d4-a716-446655440000"):
                        pass

        assert exc_info.value.code == "TIMEOUT_ERROR"

    @pytest.mark.asyncio
    async def test_async_stream_mid_stream_error(self):
        """StreamError during iteration should yield a stream_error event."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def aiter_lines_with_error():
            yield "event: step"
            yield 'data: {"step_id": "s1"}'
            yield ""
            raise httpx.StreamError("Broken pipe")

        mock_resp.aiter_lines = aiter_lines_with_error
        mock_resp.aread = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(httpx.AsyncClient, "stream", return_value=mock_ctx):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                events = []
                async for event in client.stream("550e8400-e29b-41d4-a716-446655440000"):
                    events.append(event)

        assert len(events) == 2
        assert events[0]["_event"] == "step"
        assert events[1]["_event"] == "stream_error"
        assert "Broken pipe" in events[1]["message"]


# ===========================================================================
# Section 5: CLI error formatting (improved SandcastleError handling)
# ===========================================================================


class TestFormatCliError:
    """Tests for _format_cli_error with SandcastleError."""

    def test_sandcastle_error_401(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(401, "AUTH_REQUIRED", "API key required")
        msg = _format_cli_error(exc)
        assert "Authentication failed" in msg
        assert "API key required" in msg
        assert "HTTP 401" in msg

    def test_sandcastle_error_403(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(403, "FORBIDDEN", "Admin access required")
        msg = _format_cli_error(exc)
        assert "Access denied" in msg
        assert "Admin access required" in msg

    def test_sandcastle_error_404(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(404, "NOT_FOUND", "Run not found")
        msg = _format_cli_error(exc)
        assert "Not found" in msg
        assert "Run not found" in msg

    def test_sandcastle_error_422(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(422, "VALIDATION_ERROR", "Missing required field: workflow_name")
        msg = _format_cli_error(exc)
        assert "Validation error" in msg
        assert "Missing required field" in msg

    def test_sandcastle_error_500(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(500, "INTERNAL_ERROR", "Database connection lost")
        msg = _format_cli_error(exc)
        assert "Server error" in msg
        assert "HTTP 500" in msg

    def test_sandcastle_error_generic(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(429, "RATE_LIMITED", "Too many requests")
        msg = _format_cli_error(exc)
        assert "API error" in msg
        assert "HTTP 429" in msg
        assert "RATE_LIMITED" in msg
        assert "Too many requests" in msg

    def test_sandcastle_error_non_http(self):
        from sandcastle.__main__ import _format_cli_error

        exc = SandcastleError(0, "CONNECTION_ERROR", "Failed to connect")
        msg = _format_cli_error(exc)
        assert "API error" in msg
        assert "CONNECTION_ERROR" in msg

    def test_connection_error_still_works(self):
        from sandcastle.__main__ import _format_cli_error

        exc = httpx.ConnectError("refused")
        msg = _format_cli_error(exc)
        assert "Connection failed" in msg
        assert "sandcastle serve" in msg

    def test_file_not_found_still_works(self):
        from sandcastle.__main__ import _format_cli_error

        exc = FileNotFoundError("workflow.yaml")
        msg = _format_cli_error(exc)
        assert "File not found" in msg


# ===========================================================================
# Section 6: CLI doctor completeness (database & Redis checks)
# ===========================================================================


class TestDoctorDatabaseRedisChecks:
    """Tests that the doctor command checks database and Redis connectivity."""

    def _run_doctor(self, env_overrides: dict | None = None):
        """Helper to run doctor with env overrides and capture output.

        Sets environment variables directly so the real Settings() picks them up.
        """
        from sandcastle.__main__ import _cmd_doctor

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        old_env = {}
        env_overrides = env_overrides or {}

        # Set env vars (real Settings reads from env)
        for key, val in env_overrides.items():
            old_env[key] = os.environ.get(key)
            os.environ[key] = val

        try:
            _cmd_doctor(argparse.Namespace())
        except SystemExit:
            pass
        finally:
            sys.stdout = old_stdout
            # Restore old env
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

        return captured.getvalue()

    def test_doctor_checks_database_section_present(self):
        """Doctor should have a Database & Redis section."""
        output = self._run_doctor({
            "ANTHROPIC_API_KEY": "sk-test-12345678",
            "DATABASE_URL": "",
            "REDIS_URL": "",
        })
        assert "Database & Redis" in output

    def test_doctor_checks_sqlite_default(self):
        """Doctor should report SQLite when database_url is empty."""
        output = self._run_doctor({
            "ANTHROPIC_API_KEY": "sk-test-12345678",
            "DATABASE_URL": "",
            "REDIS_URL": "",
        })
        assert "Database" in output
        assert "sqlite" in output.lower()

    def test_doctor_checks_postgres_configured(self):
        """Doctor should report PostgreSQL when database_url starts with postgresql."""
        output = self._run_doctor({
            "ANTHROPIC_API_KEY": "sk-test-12345678",
            "DATABASE_URL": "postgresql://user:pass@db-host:5432/sandcastle",
            "REDIS_URL": "",
        })
        assert "PostgreSQL" in output

    def test_doctor_checks_redis_configured(self):
        """Doctor should check Redis connectivity when redis_url is set."""
        output = self._run_doctor({
            "ANTHROPIC_API_KEY": "sk-test-12345678",
            "DATABASE_URL": "",
            "REDIS_URL": "redis://redis-host:6379",
        })
        assert "Redis" in output
        assert "redis-host" in output

    def test_doctor_no_redis_reports_in_process(self):
        """Doctor should report in-process queue when Redis is not configured."""
        output = self._run_doctor({
            "ANTHROPIC_API_KEY": "sk-test-12345678",
            "DATABASE_URL": "",
            "REDIS_URL": "",
        })
        assert "in-process queue" in output


# ===========================================================================
# Section 7: SDK approve/reject API error handling
# ===========================================================================


class TestApproveRejectErrors:
    """Tests for error handling in approve/reject methods."""

    def test_approve_api_error_raises(self):
        mock_resp = _mock_response(
            status_code=404,
            json_data={
                "detail": {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Approval not found",
                    }
                }
            },
            text="Not Found",
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.approve("nonexistent-id")
            client.close()

        assert exc_info.value.status_code == 404
        assert "NOT_FOUND" in exc_info.value.code

    def test_reject_api_error_raises(self):
        mock_resp = _mock_response(
            status_code=400,
            json_data={
                "detail": {
                    "error": {
                        "code": "ALREADY_RESOLVED",
                        "message": "Approval already resolved",
                    }
                }
            },
            text="Bad Request",
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.reject("already-resolved")
            client.close()

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_async_approve_api_error(self):
        mock_resp = _mock_response(
            status_code=404,
            json_data={
                "detail": {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Approval not found",
                    }
                }
            },
            text="Not Found",
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                with pytest.raises(SandcastleError):
                    await client.approve("nonexistent")

    @pytest.mark.asyncio
    async def test_async_reject_api_error(self):
        mock_resp = _mock_response(
            status_code=400,
            json_data={
                "detail": {
                    "error": {
                        "code": "ALREADY_RESOLVED",
                        "message": "Already resolved",
                    }
                }
            },
            text="Bad Request",
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                with pytest.raises(SandcastleError):
                    await client.reject("already-resolved")


# ===========================================================================
# Section 8: SDK list_approvals non-JSON response handling
# ===========================================================================


class TestListApprovalsNonJson:
    def test_list_approvals_non_json_raises_error(self):
        """Non-JSON 200 response should raise SandcastleError."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "<html>Bad Gateway</html>"
        resp.json.side_effect = ValueError("No JSON")

        with patch.object(httpx.Client, "get", return_value=resp):
            client = SandcastleClient(base_url="http://test:8080")
            with pytest.raises(SandcastleError) as exc_info:
                client.list_approvals()
            client.close()

        assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_async_list_approvals_non_json_raises_error(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("No JSON")

        with patch.object(httpx.AsyncClient, "get", return_value=resp):
            async with AsyncSandcastleClient(base_url="http://test:8080") as client:
                with pytest.raises(SandcastleError) as exc_info:
                    await client.list_approvals()

        assert exc_info.value.code == "INVALID_RESPONSE"


# ===========================================================================
# Section 9: Approval dataclass is exported from SDK module
# ===========================================================================


class TestApprovalExport:
    """Verify Approval is importable from the SDK."""

    def test_approval_is_importable(self):
        from sandcastle.sdk import Approval
        assert Approval is not None

    def test_parse_approval_is_importable(self):
        from sandcastle.sdk import _parse_approval
        assert callable(_parse_approval)
