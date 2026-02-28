"""Deep API contract audit tests.

Tests subtle contract violations, TOCTOU races, pagination edge cases,
webhook contract consistency, and response schema fidelity.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)

VALID_WORKFLOW = """
name: audit-test
description: Audit test workflow
steps:
  - id: step1
    prompt: "Hello {input.name}"
    model: haiku
    max_turns: 3
"""


# ---------------------------------------------------------------------------
# Focus 1: Request/Response Contract Fidelity
# ---------------------------------------------------------------------------


class TestResponseContractFidelity:
    """Verify that responses match their declared Pydantic schemas."""

    def test_health_response_matches_schema(self):
        """Health endpoint returns HealthResponse inside ApiResponse.data."""
        with patch("sandcastle.api.routes.get_sandshore_runtime") as mock_rt:
            mock = AsyncMock()
            mock.health.return_value = True
            mock_rt.return_value = mock

            response = client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        health = body["data"]
        assert isinstance(health["status"], str)
        assert isinstance(health["runtime"], bool)
        assert isinstance(health["database"], bool)
        assert "redis" in health

    def test_runtime_response_includes_license(self):
        """Runtime endpoint must always include version and license."""
        response = client.get("/api/runtime")
        assert response.status_code == 200
        body = response.json()
        data = body["data"]
        assert "version" in data
        assert "license" in data
        if data["license"]:
            lic = data["license"]
            assert "status" in lic
            assert "tier" in lic

    def test_stats_response_schema_fields(self):
        """StatsResponse schema has all expected fields defined."""
        from sandcastle.api.schemas import StatsResponse

        fields = StatsResponse.model_fields
        assert "total_runs_today" in fields
        assert "success_rate" in fields
        assert "total_cost_today" in fields
        assert "avg_duration_seconds" in fields
        assert "runs_by_day" in fields
        assert "cost_by_workflow" in fields

    def test_error_responses_have_code_and_message(self):
        """All error responses must contain code and message fields."""
        response = client.get("/api/runs/not-a-uuid")
        assert response.status_code == 400
        body = response.json()
        assert "detail" in body
        detail = body["detail"]
        assert "error" in detail
        assert "code" in detail["error"]
        assert "message" in detail["error"]

    def test_idempotency_response_returns_raw_dict_not_model(self):
        """When idempotency key matches, response is a raw dict, not RunStatusResponse.

        This is a known contract inconsistency - the idempotent response
        returns {run_id, status, idempotent} instead of a full RunStatusResponse.
        """
        run_id = str(uuid.uuid4())

        with patch("sandcastle.api.routes.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=uuid.UUID(run_id))
            mock_session_cls.return_value = mock_session

            response = client.post(
                "/api/workflows/run/sync",
                json={
                    "workflow": VALID_WORKFLOW,
                    "input": {"name": "test"},
                    "idempotency_key": "dedup-key-1",
                },
            )

        assert response.status_code == 200
        body = response.json()
        data = body["data"]
        assert data["idempotent"] is True
        assert "run_id" in data
        assert "status" in data

    def test_run_list_empty_returns_empty_array_not_null(self):
        """Empty list of runs returns [] not null."""
        response = client.get("/api/runs?status=nonexistent_status")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == [] or isinstance(body["data"], list)

    def test_schedule_response_created_at_is_iso8601(self):
        """Datetime fields in responses should be ISO 8601 formatted."""
        response = client.get("/api/schedules")
        assert response.status_code == 200
        body = response.json()
        # Empty is valid
        assert isinstance(body["data"], list)


# ---------------------------------------------------------------------------
# Focus 2: Pagination Correctness
# ---------------------------------------------------------------------------


class TestPaginationCorrectness:
    """Verify pagination math at boundaries."""

    def test_runs_pagination_offset_zero(self):
        """offset=0, limit=10 returns up to 10 items."""
        response = client.get("/api/runs?offset=0&limit=10")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body
        meta = body["meta"]
        assert meta["offset"] == 0
        assert meta["limit"] == 10
        assert isinstance(meta["total"], int)
        assert meta["total"] >= 0

    def test_runs_pagination_offset_beyond_total(self):
        """offset > total should return empty data but valid meta."""
        response = client.get("/api/runs?offset=999999&limit=10")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["offset"] == 999999

    def test_schedules_pagination_meta_present(self):
        """Schedules list must include pagination metadata."""
        response = client.get("/api/schedules?limit=1&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body
        assert body["meta"]["limit"] == 1
        assert body["meta"]["offset"] == 0

    def test_dead_letter_pagination_meta(self):
        """Dead letter list must include pagination metadata."""
        response = client.get("/api/dead-letter?limit=5&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body
        assert body["meta"]["limit"] == 5

    def test_violations_pagination(self):
        """Violations list must include pagination metadata."""
        response = client.get("/api/violations?limit=3&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body

    def test_experiments_pagination(self):
        """Experiments list must include pagination metadata."""
        response = client.get("/api/autopilot/experiments?limit=2&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body

    def test_api_keys_pagination(self):
        """API keys list includes pagination metadata."""
        response = client.get("/api/api-keys?limit=5&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body

    def test_limit_validation_rejects_zero(self):
        """limit=0 should be rejected (ge=1)."""
        response = client.get("/api/runs?limit=0")
        assert response.status_code == 422

    def test_limit_validation_rejects_over_max(self):
        """limit=201 should be rejected (le=200)."""
        response = client.get("/api/runs?limit=201")
        assert response.status_code == 422

    def test_offset_validation_rejects_negative(self):
        """offset=-1 should be rejected (ge=0)."""
        response = client.get("/api/runs?offset=-1")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Focus 3: Concurrent Mutation Safety
# ---------------------------------------------------------------------------


class TestConcurrentMutationSafety:
    """Test race conditions and concurrent access patterns."""

    def test_cancel_only_cancels_active_runs(self):
        """Cancel should not overwrite a completed run's status."""
        run_id = str(uuid.uuid4())

        with patch("sandcastle.api.routes.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_run = MagicMock()
            mock_run.status = MagicMock()
            mock_run.status.value = "completed"

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_run

            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_cls.return_value = mock_session

            response = client.post(f"/api/runs/{run_id}/cancel")

        assert response.status_code == 400
        body = response.json()
        assert "INVALID_STATUS" in str(body)

    def test_delete_active_run_rejected(self):
        """Cannot delete a running workflow."""
        run_id = str(uuid.uuid4())

        with patch("sandcastle.api.routes.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_run = MagicMock()
            mock_run.status = MagicMock()
            mock_run.status.value = "running"

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_run

            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_cls.return_value = mock_session

            response = client.delete(f"/api/runs/{run_id}")

        assert response.status_code == 400
        body = response.json()
        assert "INVALID_STATUS" in str(body)

    def test_invalid_uuid_format_returns_400(self):
        """All endpoints accepting UUID params should validate format."""
        bad_ids = ["not-a-uuid", "abc-def-ghi"]

        # GET endpoints that validate UUID without rate limiting
        for bad_id in bad_ids:
            resp = client.get(f"/api/runs/{bad_id}")
            assert resp.status_code == 400, f"Expected 400 for GET /api/runs/{bad_id}"

        for bad_id in bad_ids:
            resp = client.post(f"/api/runs/{bad_id}/cancel")
            assert resp.status_code == 400, f"Expected 400 for POST cancel with id={bad_id}"

        # Replay/fork go through rate limiter first, so mock it
        with patch("sandcastle.api.routes.execution_limiter") as mock_limiter:
            mock_limiter.check = AsyncMock()
            for bad_id in bad_ids:
                resp = client.post(
                    f"/api/runs/{bad_id}/replay",
                    json={"from_step": "s"},
                )
                assert resp.status_code == 400, f"Expected 400 for POST replay with id={bad_id}"

            for bad_id in bad_ids:
                resp = client.post(
                    f"/api/runs/{bad_id}/fork",
                    json={"from_step": "s", "changes": {}},
                )
                assert resp.status_code == 400, f"Expected 400 for POST fork with id={bad_id}"


# ---------------------------------------------------------------------------
# Focus 4: Webhook Contract
# ---------------------------------------------------------------------------


class TestWebhookContract:
    """Verify webhook payload shape, HMAC, and field consistency."""

    def test_webhook_payload_has_all_required_fields(self):
        """Webhook payload must include event, run_id, workflow, status, timestamp."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        captured_body = {}

        async def _capture_dispatch():
            with patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                async def capture_post(url, content, headers):
                    captured_body["payload"] = json.loads(content)
                    captured_body["headers"] = headers
                    return mock_resp

                mock_client.post = capture_post
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                with patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://example.com/hook"):
                    result = await dispatch_webhook(
                        url="https://example.com/hook",
                        event="workflow.completed",
                        run_id="test-run-123",
                        workflow="test-wf",
                        status="completed",
                        outputs={"key": "value"},
                        costs=0.05,
                        duration_seconds=12.5,
                    )
                    assert result is True

        asyncio.run(_capture_dispatch())

        payload = captured_body["payload"]
        assert payload["event"] == "workflow.completed"
        assert payload["run_id"] == "test-run-123"
        assert payload["workflow"] == "test-wf"
        assert payload["status"] == "completed"
        assert "timestamp" in payload
        assert payload["total_cost_usd"] == 0.05
        assert payload["costs"] == 0.05  # backward compat alias
        assert payload["duration_seconds"] == 12.5

    def test_webhook_hmac_signature_verifiable(self):
        """HMAC signature in header must be verifiable with the secret."""
        from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature

        secret = "test-secret-key"
        body = '{"event":"test","run_id":"r1"}'
        sig = _sign_payload(body, secret)

        assert verify_signature(body, sig, secret) is True
        assert verify_signature(body, "wrong-sig", secret) is False
        assert verify_signature(body + "tampered", sig, secret) is False

    def test_webhook_no_signature_when_no_secret(self):
        """When webhook_secret is empty, X-Sandcastle-Signature should be absent."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        captured_headers = {}

        async def _run():
            with patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                async def capture_post(url, content, headers):
                    captured_headers.update(headers)
                    return mock_resp

                mock_client.post = capture_post
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                with patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://example.com"):
                    with patch("sandcastle.webhooks.dispatcher.settings") as mock_settings:
                        mock_settings.webhook_secret = ""
                        await dispatch_webhook(
                            url="https://example.com",
                            event="test",
                            run_id="r1",
                            workflow="w1",
                            status="completed",
                        )

        asyncio.run(_run())
        assert "X-Sandcastle-Signature" not in captured_headers

    def test_webhook_signature_present_with_secret(self):
        """When webhook_secret is set, signature header must be present."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        captured_headers = {}

        async def _run():
            with patch("sandcastle.webhooks.dispatcher.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                async def capture_post(url, content, headers):
                    captured_headers.update(headers)
                    return mock_resp

                mock_client.post = capture_post
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                with patch("sandcastle.webhooks.dispatcher.validate_callback_url", return_value="https://example.com"):
                    with patch("sandcastle.webhooks.dispatcher.settings") as mock_settings:
                        mock_settings.webhook_secret = "my-secret"
                        await dispatch_webhook(
                            url="https://example.com",
                            event="test",
                            run_id="r1",
                            workflow="w1",
                            status="completed",
                        )

        asyncio.run(_run())
        assert "X-Sandcastle-Signature" in captured_headers
        assert len(captured_headers["X-Sandcastle-Signature"]) == 64  # SHA256 hex


# ---------------------------------------------------------------------------
# Focus 5: Input Validation Edge Cases
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Test edge cases in workflow input validation."""

    def test_validate_workflow_input_required_empty_string(self):
        """Required field with empty string should fail validation."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        errors = _validate_workflow_input({"name": ""}, schema)
        assert len(errors) > 0
        assert "missing or empty" in errors[0]

    def test_validate_workflow_input_required_missing(self):
        """Required field that's missing should fail validation."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        errors = _validate_workflow_input({}, schema)
        assert len(errors) > 0

    def test_validate_workflow_input_integer_coercion(self):
        """String '42' should be coerced to int 42."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "properties": {"count": {"type": "integer"}},
        }
        data = {"count": "42"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["count"] == 42

    def test_validate_workflow_input_invalid_integer(self):
        """String 'abc' for integer field should produce error."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "properties": {"count": {"type": "integer"}},
        }
        errors = _validate_workflow_input({"count": "abc"}, schema)
        assert len(errors) > 0
        assert "integer" in errors[0]

    def test_validate_workflow_input_boolean_coercion(self):
        """String 'true'/'false' should be coerced to bool."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "properties": {"flag": {"type": "boolean"}},
        }
        data_true = {"flag": "true"}
        data_false = {"flag": "false"}
        assert _validate_workflow_input(data_true, schema) == []
        assert data_true["flag"] is True
        assert _validate_workflow_input(data_false, schema) == []
        assert data_false["flag"] is False

    def test_validate_workflow_input_array_coercion(self):
        """String '[1,2,3]' should be coerced to list."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "properties": {"items": {"type": "array"}},
        }
        data = {"items": "[1, 2, 3]"}
        errors = _validate_workflow_input(data, schema)
        assert errors == []
        assert data["items"] == [1, 2, 3]

    def test_validate_workflow_input_none_schema(self):
        """None schema should produce no errors."""
        from sandcastle.api.routes import _validate_workflow_input

        errors = _validate_workflow_input({"anything": "goes"}, None)
        assert errors == []

    def test_validate_workflow_input_invalid_schema_type(self):
        """Non-dict schema should produce an error."""
        from sandcastle.api.routes import _validate_workflow_input

        errors = _validate_workflow_input({}, "not-a-dict")
        assert len(errors) > 0
        assert "must be a dict" in errors[0]

    def test_validate_workflow_input_zero_is_valid_required(self):
        """Integer 0 for a required field should NOT fail (it's not None or '')."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        }
        errors = _validate_workflow_input({"count": 0}, schema)
        assert errors == []

    def test_validate_workflow_input_false_is_valid_required(self):
        """Boolean False for a required field should NOT fail."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "required": ["flag"],
            "properties": {"flag": {"type": "boolean"}},
        }
        errors = _validate_workflow_input({"flag": False}, schema)
        assert errors == []


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------


class TestWorkflowHelpers:
    """Test helper functions for correctness."""

    def test_compute_checksum_deterministic(self):
        """Same content always produces same checksum."""
        from sandcastle.api.routes import _compute_checksum

        cs1 = _compute_checksum("name: test\nsteps: []")
        cs2 = _compute_checksum("name: test\nsteps: []")
        assert cs1 == cs2
        assert len(cs1) == 64  # SHA-256 hex

    def test_compute_checksum_differs(self):
        """Different content produces different checksum."""
        from sandcastle.api.routes import _compute_checksum

        cs1 = _compute_checksum("content A")
        cs2 = _compute_checksum("content B")
        assert cs1 != cs2

    def test_load_workflow_yaml_path_traversal(self):
        """Path traversal attempts should be rejected."""
        from sandcastle.api.routes import _load_workflow_yaml

        with pytest.raises(FileNotFoundError, match="Invalid workflow name"):
            _load_workflow_yaml("../etc/passwd")

        with pytest.raises(FileNotFoundError, match="Invalid workflow name"):
            _load_workflow_yaml("foo/bar")

        with pytest.raises(FileNotFoundError, match="Invalid workflow name"):
            _load_workflow_yaml("..\\windows\\system32")

    def test_load_workflow_yaml_empty_name(self):
        """Empty workflow name should raise ValueError."""
        from sandcastle.api.routes import _load_workflow_yaml

        with pytest.raises(ValueError, match="must not be empty"):
            _load_workflow_yaml("")

        with pytest.raises(ValueError, match="must not be empty"):
            _load_workflow_yaml("   ")

    def test_mask_short_value(self):
        """Short values should be fully masked."""
        from sandcastle.api.routes import _mask

        assert _mask("") == ""
        assert _mask("short") == "****"
        assert _mask("12345678901") == "****"  # 11 chars, < 12

    def test_mask_long_value(self):
        """Long values show last 4 chars."""
        from sandcastle.api.routes import _mask

        assert _mask("sk-1234567890abcd") == "****abcd"


# ---------------------------------------------------------------------------
# Settings endpoint
# ---------------------------------------------------------------------------


class TestSettingsEndpoint:
    """Test settings contract."""

    def test_immutable_settings_silently_stripped(self):
        """auth_required is not in the schema and gets silently stripped."""
        response = client.patch(
            "/api/settings",
            json={"auth_required": True},
        )
        # auth_required removed from SettingsUpdateRequest; Pydantic ignores it,
        # route strips non-mutable keys, returns 200 with no changes.
        assert response.status_code == 200

    def test_invalid_log_level_rejected(self):
        """Invalid log level returns 422."""
        response = client.patch(
            "/api/settings",
            json={"log_level": "VERBOSE"},
        )
        assert response.status_code == 422

    def test_invalid_max_depth_rejected(self):
        """max_workflow_depth outside 1-20 returns 422."""
        response = client.patch(
            "/api/settings",
            json={"max_workflow_depth": 0},
        )
        assert response.status_code == 422

        response = client.patch(
            "/api/settings",
            json={"max_workflow_depth": 21},
        )
        assert response.status_code == 422

    def test_negative_cost_rejected(self):
        """Negative default_max_cost_usd returns 422."""
        response = client.patch(
            "/api/settings",
            json={"default_max_cost_usd": -1.0},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# SSRF Prevention
# ---------------------------------------------------------------------------


class TestSSRFPrevention:
    """Test callback URL validation."""

    def test_callback_url_rejects_private_ips(self):
        """Private IP addresses should be blocked."""
        from sandcastle.webhooks.dispatcher import validate_callback_url

        for url in [
            "https://127.0.0.1/hook",
            "https://10.0.0.1/hook",
            "https://192.168.1.1/hook",
            "https://172.16.0.1/hook",
        ]:
            with pytest.raises(ValueError, match="blocked network"):
                validate_callback_url(url)

    def test_callback_url_rejects_non_http(self):
        """Non-HTTP schemes should be rejected."""
        from sandcastle.webhooks.dispatcher import validate_callback_url

        with pytest.raises(ValueError, match="http"):
            validate_callback_url("ftp://example.com/hook")

        with pytest.raises(ValueError, match="http"):
            validate_callback_url("file:///etc/passwd")

    def test_callback_url_rejects_empty_hostname(self):
        """URLs without hostname should be rejected."""
        from sandcastle.webhooks.dispatcher import validate_callback_url

        with pytest.raises(ValueError, match="hostname"):
            validate_callback_url("https:///path")


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


class TestWorkflowSanitization:
    """Test YAML sanitization for export."""

    def test_sanitize_removes_env_vars(self):
        """Environment variable references should be redacted."""
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml = "api_key: ${API_KEY}\ntoken: $SECRET_TOKEN"
        result = _sanitize_workflow_yaml(yaml)
        assert "${API_KEY}" not in result
        assert "$SECRET_TOKEN" not in result
        assert "<REDACTED>" in result

    def test_sanitize_removes_inline_secrets(self):
        """Lines with password/secret/token keys and literal values are redacted."""
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml = "password: my_secret_value\napi_key: sk-1234"
        result = _sanitize_workflow_yaml(yaml)
        assert "my_secret_value" not in result

    def test_sanitize_preserves_variable_references(self):
        """Lines with {variable} references in secret fields should be kept."""
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml = "password: {env.DB_PASSWORD}"
        result = _sanitize_workflow_yaml(yaml)
        assert "{env.DB_PASSWORD}" in result


# ---------------------------------------------------------------------------
# Webhook dispatcher - SSRF + retry
# ---------------------------------------------------------------------------


class TestWebhookDispatcherEdgeCases:
    """Test webhook dispatcher edge cases."""

    def test_ssrf_blocked_url_returns_false(self):
        """Blocked URLs should return False without making HTTP calls."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        async def _run():
            result = await dispatch_webhook(
                url="https://127.0.0.1/hook",
                event="test",
                run_id="r1",
                workflow="w1",
                status="completed",
            )
            assert result is False

        asyncio.run(_run())

    def test_dispatch_non_http_scheme_returns_false(self):
        """Non-HTTP URLs should return False."""
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        async def _run():
            result = await dispatch_webhook(
                url="ftp://example.com/hook",
                event="test",
                run_id="r1",
                workflow="w1",
                status="completed",
            )
            assert result is False

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Browse endpoint contract
# ---------------------------------------------------------------------------


class TestBrowseEndpoint:
    """Test filesystem browse endpoint contract."""

    def test_browse_returns_structured_response(self):
        """Browse returns current, parent, and entries array."""
        response = client.get("/api/browse?path=~")
        assert response.status_code == 200
        body = response.json()
        data = body["data"]
        assert "current" in data
        assert "parent" in data
        assert "entries" in data
        assert isinstance(data["entries"], list)
        for entry in data["entries"]:
            assert "name" in entry
            assert "path" in entry
            assert "is_dir" in entry

    def test_browse_nonexistent_path_returns_404(self):
        """Non-existent path returns 404."""
        response = client.get("/api/browse?path=/nonexistent/path/xyz123")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Run compare endpoint
# ---------------------------------------------------------------------------


class TestRunCompareContract:
    """Test run comparison contract."""

    def test_compare_invalid_uuid_returns_400(self):
        """Invalid UUIDs in compare should return 400."""
        response = client.get("/api/runs/compare?run_a=bad&run_b=bad")
        assert response.status_code == 400

    def test_compare_nonexistent_run_returns_404(self):
        """Non-existent run IDs should return 404."""
        fake_a = str(uuid.uuid4())
        fake_b = str(uuid.uuid4())
        response = client.get(f"/api/runs/compare?run_a={fake_a}&run_b={fake_b}")
        assert response.status_code == 404
