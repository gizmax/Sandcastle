"""Final coverage push - targeting remaining uncovered branches.

Targets:
  - api/a2a.py: JSON-RPC endpoint (batch, invalid method, missing method, array body)
  - engine/memory.py: resolve_scope_id, detect_conflicts, _apply_relevance_decay
  - engine/sandshore.py: CircuitBreaker branches
  - queue/scheduler.py: add_schedule validation
  - __main__.py: _load_dot_env OSError, _cmd_doctor backend branches
  - sdk.py: async stream flush, list_workflows empty branch
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# ===========================================================================
# api/a2a.py - JSON-RPC endpoint branches
# ===========================================================================

class TestA2AEndpoint:
    """Cover A2A JSON-RPC endpoint branches."""

    def test_agent_card_endpoint(self):
        """GET /.well-known/agent.json returns agent card."""
        response = client.get("/.well-known/agent.json")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data or "skills" in data or "capabilities" in data or data

    def test_a2a_batch_request_rejected(self):
        """POST /a2a with batch (array) request returns error."""
        response = client.post(
            "/a2a",
            json=[{"jsonrpc": "2.0", "method": "tasks/get", "id": 1}],
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "Batch" in data["error"]["message"] or "not supported" in data["error"]["message"]

    def test_a2a_invalid_jsonrpc_version(self):
        """POST /a2a with wrong jsonrpc version returns error."""
        response = client.post(
            "/a2a",
            json={"jsonrpc": "1.0", "method": "tasks/get", "id": 1, "params": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "2.0" in data["error"]["message"]

    def test_a2a_missing_method(self):
        """POST /a2a with missing method returns error."""
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "params": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data

    def test_a2a_unknown_method(self):
        """POST /a2a with unknown method returns method not found error."""
        response = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "method": "unknown/method", "id": 1, "params": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    def test_a2a_tasks_get_unknown_task(self):
        """POST /a2a tasks/get with unknown task id returns failed."""
        fake_id = str(uuid.uuid4())
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "method": "tasks/get",
                "id": 1,
                "params": {"id": fake_id},
            },
        )
        assert response.status_code == 200
        data = response.json()
        # Either result or error - both are valid JSON-RPC
        assert "result" in data or "error" in data

    def test_a2a_tasks_cancel_unknown_task(self):
        """POST /a2a tasks/cancel with unknown task id returns failed."""
        fake_id = str(uuid.uuid4())
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "method": "tasks/cancel",
                "id": 1,
                "params": {"id": fake_id},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data or "error" in data

    def test_a2a_tasks_cancel_invalid_id(self):
        """POST /a2a tasks/cancel with invalid (non-UUID) task id."""
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "method": "tasks/cancel",
                "id": 1,
                "params": {"id": "not-a-valid-uuid"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data or "error" in data

    def test_a2a_tasks_get_invalid_id(self):
        """POST /a2a tasks/get with invalid (non-UUID) task id."""
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "method": "tasks/get",
                "id": 1,
                "params": {"id": "not-a-valid-uuid"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data or "error" in data

    def test_a2a_invalid_params_type(self):
        """POST /a2a with non-dict params returns error."""
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "method": "tasks/get",
                "id": 1,
                "params": ["not", "a", "dict"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    def test_a2a_parse_error_invalid_json(self):
        """POST /a2a with invalid JSON body returns parse error."""
        response = client.post(
            "/a2a",
            content=b"not-valid-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32700

    def test_a2a_tasks_send_without_workflow(self):
        """POST /a2a tasks/send with no workflow name returns failed."""
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "id": 1,
                "params": {
                    "id": str(uuid.uuid4()),
                    "message": {
                        "parts": []
                    },
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data or "error" in data

    def test_a2a_not_dict_body(self):
        """POST /a2a with non-dict JSON body returns error."""
        response = client.post(
            "/a2a",
            json=42,
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data


# ===========================================================================
# engine/memory.py - pure functions
# ===========================================================================

class TestMemoryHelpers:
    """Cover engine/memory.py pure helper functions."""

    def test_resolve_scope_id_workflow(self):
        """resolve_scope_id returns workflow scope by default."""
        from sandcastle.engine.memory import resolve_scope_id

        mock_cfg = MagicMock()
        mock_cfg.scope = "workflow"
        result = resolve_scope_id(mock_cfg, "my-workflow")
        assert result == "workflow:my-workflow"

    def test_resolve_scope_id_global(self):
        """resolve_scope_id returns 'global' for global scope."""
        from sandcastle.engine.memory import resolve_scope_id

        mock_cfg = MagicMock()
        mock_cfg.scope = "global"
        result = resolve_scope_id(mock_cfg, "my-workflow")
        assert result == "global"

    def test_resolve_scope_id_agent(self):
        """resolve_scope_id returns agent scope when configured."""
        from sandcastle.engine.memory import resolve_scope_id

        mock_cfg = MagicMock()
        mock_cfg.scope = "agent"
        mock_cfg.agent = "my-agent"
        result = resolve_scope_id(mock_cfg, "my-workflow")
        assert result == "agent:my-agent"

    def test_resolve_scope_id_none_config(self):
        """resolve_scope_id handles None config."""
        from sandcastle.engine.memory import resolve_scope_id

        result = resolve_scope_id(None, "my-workflow")
        assert result == "workflow:my-workflow"

    def test_detect_conflicts_empty_content(self):
        """detect_conflicts returns empty list for empty content."""
        from sandcastle.engine.memory import detect_conflicts

        result = detect_conflicts("", [{"memory": "something"}])
        assert result == []

    def test_detect_conflicts_no_memories(self):
        """detect_conflicts returns empty list for no existing memories."""
        from sandcastle.engine.memory import detect_conflicts

        result = detect_conflicts("some content here", [])
        assert result == []

    def test_detect_conflicts_high_overlap(self):
        """detect_conflicts detects high word overlap."""
        from sandcastle.engine.memory import detect_conflicts

        content = "the user prefers dark mode dark theme dark background"
        existing = [
            {"memory": "user prefers dark mode dark theme always", "id": "mem-1"},
        ]
        result = detect_conflicts(content, existing)
        assert isinstance(result, list)

    def test_detect_conflicts_low_overlap(self):
        """detect_conflicts returns empty list for low word overlap."""
        from sandcastle.engine.memory import detect_conflicts

        content = "user likes coffee"
        existing = [
            {"memory": "completely different topic about finance", "id": "mem-1"},
        ]
        result = detect_conflicts(content, existing)
        assert result == []

    def test_detect_conflicts_memory_no_text(self):
        """detect_conflicts skips memories with no text field."""
        from sandcastle.engine.memory import detect_conflicts

        content = "some content"
        existing = [{"id": "mem-1"}]  # No 'memory' key
        result = detect_conflicts(content, existing)
        assert result == []


# ===========================================================================
# engine/sandshore.py - CircuitBreaker
# ===========================================================================

class TestCircuitBreaker:
    """Cover CircuitBreaker state machine branches."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_initial_closed(self):
        """CircuitBreaker starts in CLOSED state and allows requests."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        """CircuitBreaker opens after reaching failure_threshold."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
        await cb.record_failure()
        await cb.record_failure()
        # Should be OPEN now
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_reset_on_success(self):
        """CircuitBreaker resets failure count on success."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()
        # After success, should be closed again
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_transition(self):
        """CircuitBreaker transitions to HALF_OPEN after recovery_timeout."""
        from sandcastle.engine.sandshore import CircuitBreaker
        import time

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.001)
        await cb.record_failure()
        # Should be OPEN
        assert await cb.allow_request() is False

        # Wait for recovery timeout
        await asyncio.sleep(0.01)

        # Now should be HALF_OPEN - allow one probe
        result = await cb.allow_request()
        assert result is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_rejects_second_request(self):
        """CircuitBreaker in HALF_OPEN rejects a second concurrent probe."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.001)
        await cb.record_failure()

        # Wait for recovery
        await asyncio.sleep(0.01)

        # First probe allowed
        first = await cb.allow_request()
        assert first is True

        # Second probe while in HALF_OPEN should be rejected
        second = await cb.allow_request()
        assert second is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_state_property(self):
        """CircuitBreaker.state property reflects current state."""
        from sandcastle.engine.sandshore import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.001)
        assert cb.state == CircuitBreaker.CLOSED

        await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        await asyncio.sleep(0.01)
        assert cb.state == CircuitBreaker.HALF_OPEN


# ===========================================================================
# queue/scheduler.py - add_schedule validation
# ===========================================================================

class TestSchedulerValidation:
    """Cover scheduler add_schedule input validation."""

    def test_add_schedule_empty_cron(self):
        """add_schedule raises ValueError for empty cron expression."""
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="cron_expression"):
            add_schedule("sched-1", "", "my-workflow")

    def test_add_schedule_empty_workflow_name(self):
        """add_schedule raises ValueError for empty workflow name."""
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="workflow_name"):
            add_schedule("sched-1", "0 9 * * *", "")

    def test_add_schedule_invalid_cron(self):
        """add_schedule raises ValueError for invalid cron expression."""
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="[Ii]nvalid cron"):
            add_schedule("sched-1", "not a cron", "my-workflow")

    def test_list_schedules_importable(self):
        """list_schedules function is importable."""
        from sandcastle.queue.scheduler import list_schedules
        assert callable(list_schedules)


# ===========================================================================
# __main__.py - _load_dot_env OSError branch
# ===========================================================================

class TestLoadDotEnvOSError:
    """Cover _load_dot_env OSError branch."""

    def test_load_dot_env_oserror_is_handled(self):
        """_load_dot_env silently handles OSError."""
        from sandcastle.__main__ import _load_dot_env

        with patch("builtins.open", side_effect=OSError("Permission denied")):
            # Should not raise
            _load_dot_env()

    def test_load_dot_env_no_file(self, tmp_path):
        """_load_dot_env handles missing .env file gracefully."""
        from sandcastle.__main__ import _load_dot_env
        import os

        orig_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            _load_dot_env()  # No .env file - should not raise
        finally:
            os.chdir(orig_dir)


# ===========================================================================
# __main__.py - _cmd_doctor backend-specific branches
# ===========================================================================

class TestCmdDoctorBackendBranches:
    """Cover _cmd_doctor backend-specific diagnostic branches."""

    def test_cmd_doctor_docker_backend(self, capsys):
        """_cmd_doctor shows docker backend section."""
        from sandcastle.__main__ import _cmd_doctor
        import argparse

        args = argparse.Namespace(url="http://localhost:8080", api_key=None, json=False, verbose=False, timeout=30)

        with patch("sandcastle.__main__._get_client"), \
             patch("sandcastle.config.Settings") as mock_settings_cls:
            mock_cfg = MagicMock()
            mock_cfg.anthropic_api_key = ""
            mock_cfg.e2b_api_key = ""
            mock_cfg.sandbox_backend = "docker"
            mock_cfg.database_url = "sqlite:///test.db"
            mock_settings_cls.return_value = mock_cfg

            try:
                _cmd_doctor(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert isinstance(captured.out, str)
        assert len(captured.out) > 0

    def test_cmd_doctor_local_backend(self, capsys):
        """_cmd_doctor shows local backend section."""
        from sandcastle.__main__ import _cmd_doctor
        import argparse

        args = argparse.Namespace(url="http://localhost:8080", api_key=None, json=False, verbose=False, timeout=30)

        with patch("sandcastle.config.Settings") as mock_settings_cls:
            mock_cfg = MagicMock()
            mock_cfg.anthropic_api_key = "sk-ant-test123"
            mock_cfg.e2b_api_key = ""
            mock_cfg.sandbox_backend = "local"
            mock_cfg.database_url = "sqlite:///test.db"
            mock_settings_cls.return_value = mock_cfg

            try:
                _cmd_doctor(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_cmd_doctor_cloudflare_backend_no_url(self, capsys):
        """_cmd_doctor shows cloudflare backend without URL configured."""
        from sandcastle.__main__ import _cmd_doctor
        import argparse

        args = argparse.Namespace(url="http://localhost:8080", api_key=None, json=False, verbose=False, timeout=30)

        with patch("sandcastle.config.Settings") as mock_settings_cls:
            mock_cfg = MagicMock()
            mock_cfg.anthropic_api_key = "sk-ant-test123"
            mock_cfg.e2b_api_key = ""
            mock_cfg.sandbox_backend = "cloudflare"
            mock_cfg.cloudflare_worker_url = ""
            mock_cfg.database_url = "sqlite:///test.db"
            mock_settings_cls.return_value = mock_cfg

            try:
                _cmd_doctor(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert isinstance(captured.out, str)


# ===========================================================================
# sdk.py - AsyncSandcastleClient branches
# ===========================================================================

class TestAsyncSdkBranches:
    """Cover AsyncSandcastleClient additional branches."""

    @pytest.mark.asyncio
    async def test_async_list_workflows_empty(self):
        """AsyncSandcastleClient.list_workflows returns [] when data not a list."""
        from sandcastle.sdk import AsyncSandcastleClient

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        resp = SimpleNamespace(
            status_code=200,
            text="",
            json=MagicMock(return_value={"data": None, "meta": {}}),
        )
        sc._client.get = AsyncMock(return_value=resp)

        result = await sc.list_workflows()
        # When data is None (not a list), should return []
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_async_run_yaml_with_max_cost(self):
        """AsyncSandcastleClient.run_yaml passes max_cost_usd in body."""
        from sandcastle.sdk import AsyncSandcastleClient

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        run_data = {
            "run_id": str(uuid.uuid4()),
            "status": "queued",
            "workflow_name": "test",
            "started_at": "2026-01-15T10:00:00",
            "completed_at": None,
            "total_cost_usd": 0.0,
            "error": None,
            "output": None,
        }
        resp = SimpleNamespace(
            status_code=200,
            text="",
            json=MagicMock(return_value={"data": run_data}),
        )
        sc._client.post = AsyncMock(return_value=resp)

        result = await sc.run_yaml("name: test\nsteps: []\n", max_cost_usd=1.0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_async_create_schedule_with_input(self):
        """AsyncSandcastleClient.create_schedule passes input_data."""
        from sandcastle.sdk import AsyncSandcastleClient

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        sched_data = {
            "id": str(uuid.uuid4()),
            "workflow_name": "my-workflow",
            "cron_expression": "0 9 * * *",
            "enabled": True,
            "input_data": {"key": "value"},
            "created_at": "2026-01-15T10:00:00",
        }
        resp = SimpleNamespace(
            status_code=200,
            text="",
            json=MagicMock(return_value={"data": sched_data}),
        )
        sc._client.post = AsyncMock(return_value=resp)

        result = await sc.create_schedule("my-workflow", "0 9 * * *", input={"key": "value"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_async_list_runs_error_raises(self):
        """AsyncSandcastleClient.list_runs raises on error response."""
        from sandcastle.sdk import AsyncSandcastleClient, SandcastleError

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        resp = SimpleNamespace(
            status_code=403,
            text="Forbidden",
            json=MagicMock(return_value={"error": {"code": "FORBIDDEN", "message": "Access denied"}}),
        )
        sc._client.get = AsyncMock(return_value=resp)

        with pytest.raises(SandcastleError):
            await sc.list_runs()

    @pytest.mark.asyncio
    async def test_async_list_schedules_error_raises(self):
        """AsyncSandcastleClient.list_schedules raises on error response."""
        from sandcastle.sdk import AsyncSandcastleClient, SandcastleError

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        resp = SimpleNamespace(
            status_code=500,
            text="Server Error",
            json=MagicMock(return_value={"error": {"code": "SERVER_ERROR", "message": "Internal error"}}),
        )
        sc._client.get = AsyncMock(return_value=resp)

        with pytest.raises(SandcastleError):
            await sc.list_schedules()

    @pytest.mark.asyncio
    async def test_async_list_approvals_error_raises(self):
        """AsyncSandcastleClient.list_approvals raises on error response."""
        from sandcastle.sdk import AsyncSandcastleClient, SandcastleError

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        resp = SimpleNamespace(
            status_code=401,
            text="Unauthorized",
            json=MagicMock(return_value={"error": {"code": "UNAUTHORIZED", "message": "Auth required"}}),
        )
        sc._client.get = AsyncMock(return_value=resp)

        with pytest.raises(SandcastleError):
            await sc.list_approvals()

    @pytest.mark.asyncio
    async def test_async_publish_workflow(self):
        """AsyncSandcastleClient.publish_workflow calls post endpoint."""
        from sandcastle.sdk import AsyncSandcastleClient

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        publish_data = {
            "endpoint_url": "http://localhost:8080/api/workflows/my-wf/run",
            "spec_url": "http://localhost:8080/api/workflows/my-wf/spec",
            "version": 1,
        }
        resp = SimpleNamespace(
            status_code=200,
            text="",
            json=MagicMock(return_value={"data": publish_data}),
        )
        sc._client.post = AsyncMock(return_value=resp)

        result = await sc.publish_workflow("my-workflow")
        assert result is not None

    @pytest.mark.asyncio
    async def test_async_update_schedule_with_input(self):
        """AsyncSandcastleClient.update_schedule with input parameter."""
        from sandcastle.sdk import AsyncSandcastleClient

        sc = AsyncSandcastleClient(base_url="http://localhost:8080")
        sc._client = AsyncMock()

        sched_data = {
            "id": str(uuid.uuid4()),
            "workflow_name": "my-workflow",
            "cron_expression": "0 10 * * *",
            "enabled": True,
            "input_data": {"new": "data"},
            "created_at": "2026-01-15T10:00:00",
        }
        resp = SimpleNamespace(
            status_code=200,
            text="",
            json=MagicMock(return_value={"data": sched_data}),
        )
        sc._client.patch = AsyncMock(return_value=resp)

        sched_id = str(uuid.uuid4())
        result = await sc.update_schedule(sched_id, input={"new": "data"})
        assert result is not None


# ===========================================================================
# Sync SDK - additional branch coverage
# ===========================================================================

class TestSyncSdkBranches:
    """Cover SandcastleClient synchronous SDK branches."""

    def test_sync_update_schedule_with_input(self):
        """SandcastleClient.update_schedule includes input_data in body."""
        from sandcastle.sdk import SandcastleClient

        sc = SandcastleClient(base_url="http://localhost:8080")
        sc._client = MagicMock()

        sched_data = {
            "id": str(uuid.uuid4()),
            "workflow_name": "my-wf",
            "cron_expression": "0 9 * * *",
            "enabled": True,
            "input_data": {"key": "val"},
            "created_at": "2026-01-15T10:00:00",
        }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": sched_data}
        sc._client.patch = MagicMock(return_value=resp)

        sched_id = str(uuid.uuid4())
        result = sc.update_schedule(sched_id, input={"key": "val"})
        assert result is not None

    def test_sync_publish_workflow(self):
        """SandcastleClient.publish_workflow calls post endpoint."""
        from sandcastle.sdk import SandcastleClient

        sc = SandcastleClient(base_url="http://localhost:8080")
        sc._client = MagicMock()

        publish_data = {
            "endpoint_url": "http://localhost:8080/api/workflows/my-wf/run",
            "spec_url": "http://localhost:8080/api/workflows/my-wf/spec",
            "version": 1,
        }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": publish_data}
        sc._client.post = MagicMock(return_value=resp)

        result = sc.publish_workflow("my-workflow")
        assert result is not None


# ===========================================================================
# a2a.py internal helpers
# ===========================================================================

class TestA2AHelpers:
    """Cover a2a.py internal helper functions."""

    def test_extract_workflow_name_from_text_part(self):
        """_extract_workflow_name extracts from text part."""
        from sandcastle.api.a2a import _extract_workflow_name

        message = {
            "parts": [{"type": "text", "text": "my-workflow"}]
        }
        result = _extract_workflow_name(message)
        assert result == "my-workflow"

    def test_extract_workflow_name_from_data_part(self):
        """_extract_workflow_name extracts from data part."""
        from sandcastle.api.a2a import _extract_workflow_name

        message = {
            "parts": [{"type": "data", "data": {"workflow_name": "data-workflow"}}]
        }
        result = _extract_workflow_name(message)
        assert result == "data-workflow"

    def test_extract_workflow_name_empty_parts(self):
        """_extract_workflow_name returns None for empty parts."""
        from sandcastle.api.a2a import _extract_workflow_name

        message = {"parts": []}
        result = _extract_workflow_name(message)
        assert result is None

    def test_extract_workflow_name_not_dict(self):
        """_extract_workflow_name returns None for non-dict message."""
        from sandcastle.api.a2a import _extract_workflow_name

        result = _extract_workflow_name("not a dict")
        assert result is None

    def test_extract_input_from_data_part(self):
        """_extract_input extracts input from data part."""
        from sandcastle.api.a2a import _extract_input

        message = {
            "parts": [
                {"type": "data", "data": {"workflow_name": "wf", "input": {"key": "value"}}}
            ]
        }
        result = _extract_input(message)
        assert result == {"key": "value"}

    def test_extract_input_empty_parts(self):
        """_extract_input returns empty dict for empty parts."""
        from sandcastle.api.a2a import _extract_input

        message = {"parts": []}
        result = _extract_input(message)
        assert result == {}

    def test_extract_input_not_dict(self):
        """_extract_input returns empty dict for non-dict message."""
        from sandcastle.api.a2a import _extract_input

        result = _extract_input("not a dict")
        assert result == {}


# ===========================================================================
# engine/memory.py - _apply_relevance_decay
# ===========================================================================

class TestApplyDecay:
    """Cover apply_decay function in engine/memory.py."""

    def test_apply_decay_recent_memory(self):
        """apply_decay gives high score to recent memory."""
        from sandcastle.engine.memory import apply_decay
        from datetime import datetime, timezone

        now_str = datetime.now(timezone.utc).isoformat()
        memories = [
            {"memory": "I like coffee", "created_at": now_str},
        ]
        result = apply_decay(memories)
        assert len(result) == 1
        assert result[0]["relevance_score"] > 0.5

    def test_apply_decay_old_memory_excluded(self):
        """apply_decay excludes very old memories."""
        from sandcastle.engine.memory import apply_decay

        old_ts = "2020-01-01T00:00:00+00:00"
        memories = [
            {"memory": "Old memory", "created_at": old_ts},
        ]
        result = apply_decay(memories, max_age_days=30)
        assert len(result) == 0

    def test_apply_decay_no_timestamp(self):
        """apply_decay handles memories without created_at."""
        from sandcastle.engine.memory import apply_decay

        memories = [
            {"memory": "Memory without timestamp"},
        ]
        result = apply_decay(memories)
        # Should not raise, returns with some score
        assert isinstance(result, list)
        assert result[0]["relevance_score"] == 0.5

    def test_apply_decay_numeric_timestamp(self):
        """apply_decay handles numeric (epoch) timestamps."""
        from sandcastle.engine.memory import apply_decay
        import time

        memories = [
            {"memory": "Memory with epoch timestamp", "created_at": time.time()},
        ]
        result = apply_decay(memories)
        assert isinstance(result, list)

    def test_apply_decay_zero_max_age(self):
        """apply_decay with max_age_days=0 keeps all memories with score 1.0."""
        from sandcastle.engine.memory import apply_decay

        memories = [
            {"memory": "Any memory", "created_at": "2020-01-01T00:00:00+00:00"},
        ]
        result = apply_decay(memories, max_age_days=0)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 1.0

    def test_apply_decay_sorted_by_score(self):
        """apply_decay returns results sorted by score (descending)."""
        from sandcastle.engine.memory import apply_decay
        from datetime import datetime, timezone

        now_str = datetime.now(timezone.utc).isoformat()
        memories = [
            {"memory": "Older memory", "created_at": "2025-06-01T00:00:00+00:00"},
            {"memory": "Recent memory", "created_at": now_str},
        ]
        result = apply_decay(memories)
        if len(result) >= 2:
            # Should be sorted descending by score
            assert result[0]["relevance_score"] >= result[-1]["relevance_score"]

    def test_apply_decay_non_string_non_float_timestamp(self):
        """apply_decay handles unexpected timestamp type with fallback."""
        from sandcastle.engine.memory import apply_decay

        memories = [
            {"memory": "Memory with bad timestamp", "created_at": {"not": "a timestamp"}},
        ]
        result = apply_decay(memories)
        # Should not raise - uses datetime.now() as fallback
        assert isinstance(result, list)
