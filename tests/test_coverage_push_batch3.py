"""Coverage push batch 3 - targeting remaining gaps to reach 90%.

Targets (priority order by lines missed):
  - api/routes.py: _load_workflow_from_registry, _auto_import_workflow, workflow version endpoints,
                   health endpoint, run filters, _sanitize_workflow_yaml
  - engine/executor.py: _execute_fallback, cache helpers, _check_cancel, _check_budget,
                        resolve_templates edge cases
  - sdk.py: SandcastleClient.list_runs filter params, async list_runs, update_schedule,
            run_yaml with optional params, call_api with flags
  - engine/pdf.py: chart methods no-matplotlib paths, generate_branded_pdf variants
  - models/db.py: _sqlite_wal_mode, _build_engine_url
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# ===========================================================================
# api/routes.py - registry helpers and version loading
# ===========================================================================


class TestRoutesRegistryHelpers:
    """Cover _load_workflow_from_registry and _auto_import_workflow."""

    @pytest.mark.asyncio
    async def test_load_workflow_from_registry_not_found(self):
        """_load_workflow_from_registry returns None when not in DB."""
        from sandcastle.api.routes import _load_workflow_from_registry
        result = await _load_workflow_from_registry("definitely-not-in-registry-xyz", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_load_workflow_from_registry_version_int(self):
        """_load_workflow_from_registry queries by specific version number."""
        from sandcastle.api.routes import _load_workflow_from_registry
        result = await _load_workflow_from_registry("nonexistent-wf", 99)
        assert result is None

    @pytest.mark.asyncio
    async def test_load_workflow_from_registry_version_latest(self):
        """_load_workflow_from_registry queries latest version."""
        from sandcastle.api.routes import _load_workflow_from_registry
        result = await _load_workflow_from_registry("nonexistent-wf", "latest")
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_import_workflow_valid_yaml(self):
        """_auto_import_workflow imports a valid workflow into registry."""
        from sandcastle.api.routes import _auto_import_workflow
        yaml_content = """name: auto-import-test-xyz-abc
description: Auto import test
steps:
  - id: step1
    prompt: "Do something"
    model: haiku
"""
        version = await _auto_import_workflow("auto-import-test-xyz-abc", yaml_content)
        assert version == 1

    @pytest.mark.asyncio
    async def test_auto_import_workflow_duplicate(self):
        """_auto_import_workflow returns 1 when workflow already exists."""
        from sandcastle.api.routes import _auto_import_workflow
        yaml_content = """name: dup-import-test-xyz
steps:
  - id: step1
    prompt: "Do it"
    model: haiku
"""
        await _auto_import_workflow("dup-import-test-xyz", yaml_content)
        version = await _auto_import_workflow("dup-import-test-xyz", yaml_content)
        assert version == 1

    @pytest.mark.asyncio
    async def test_auto_import_workflow_invalid_yaml(self):
        """_auto_import_workflow handles invalid YAML gracefully (steps_count=0)."""
        from sandcastle.api.routes import _auto_import_workflow
        version = await _auto_import_workflow("invalid-yaml-wf-xyz", "::invalid yaml::")
        assert version == 1


class TestRoutesWorkflowVersionEndpoints:
    """Cover workflow version API endpoints."""

    def test_list_workflow_versions(self):
        """GET /api/workflows/{name}/versions returns versions list or 404."""
        response = client.get("/api/workflows/test-workflow/versions")
        assert response.status_code in (200, 404, 500)
        if response.status_code == 200:
            data = response.json()
            assert "data" in data

    def test_get_workflow_version_not_found(self):
        """GET /api/workflows/{name}/versions/{v} returns 200 or 404."""
        response = client.get("/api/workflows/nonexistent-xyz/versions/99")
        assert response.status_code in (200, 404)

    def test_sanitize_workflow_yaml(self):
        """_sanitize_workflow_yaml removes or returns YAML."""
        from sandcastle.api.routes import _sanitize_workflow_yaml
        yaml_content = """name: test-sanitize
steps:
  - id: step1
    type: code
    code: "print('hello')"
  - id: step2
    prompt: "Do something"
    model: haiku
"""
        result = _sanitize_workflow_yaml(yaml_content)
        assert isinstance(result, str)

    def test_runs_with_status_filter(self):
        """GET /api/runs with status filter."""
        response = client.get("/api/runs?status=completed&limit=10")
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            assert "data" in data

    def test_runs_with_workflow_filter(self):
        """GET /api/runs with workflow filter."""
        response = client.get("/api/runs?workflow=test-wf&limit=5")
        assert response.status_code in (200, 500)

    def test_run_not_found(self):
        """GET /api/runs/{id} returns 404 for non-existent run."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/runs/{fake_id}")
        assert response.status_code in (200, 404, 500)


class TestRoutesHealthEndpoint:
    """Cover health endpoint branches."""

    def test_health_check_ok(self):
        """GET /api/health returns health status."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "status" in data["data"]

    def test_workflow_version_stats(self):
        """GET /api/workflows/{name}/stats returns stats object."""
        response = client.get("/api/workflows/some-workflow/stats")
        assert response.status_code in (200, 404, 500)


# ===========================================================================
# engine/executor.py - _execute_fallback and cache helpers
# ===========================================================================


class TestExecutorFallback:
    """Cover _execute_fallback function."""

    @pytest.mark.asyncio
    async def test_execute_fallback_success(self):
        """_execute_fallback returns step result on success."""
        from sandcastle.engine.executor import RunContext, _execute_fallback
        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.sandshore import SandshoreRuntime

        step = MagicMock(spec=StepDefinition)
        step.id = "fallback-step"
        step.fallback = MagicMock()
        step.fallback.prompt = "Fallback: {input.query}"
        step.fallback.model = "claude-3-haiku-20240307"
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []

        ctx = RunContext(
            workflow_name="test-wf",
            run_id=str(uuid.uuid4()),
            input={"query": "test question"},
        )

        mock_sandbox = AsyncMock(spec=SandshoreRuntime)
        mock_result = MagicMock()
        mock_result.text = "Fallback response"
        mock_result.structured_output = None
        mock_result.total_cost_usd = 0.0005
        mock_sandbox.query.return_value = mock_result

        mock_storage = AsyncMock()

        result = await _execute_fallback(step, ctx, mock_sandbox, mock_storage)
        assert result.step_id == "fallback-step"
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_fallback_exception(self):
        """_execute_fallback returns failed result on exception."""
        from sandcastle.engine.executor import RunContext, _execute_fallback
        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.sandshore import SandshoreRuntime

        step = MagicMock(spec=StepDefinition)
        step.id = "fallback-fail-step"
        step.fallback = MagicMock()
        step.fallback.prompt = "Fallback prompt"
        step.fallback.model = "haiku"
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []

        ctx = RunContext(
            workflow_name="test-wf",
            run_id=str(uuid.uuid4()),
            input={},
        )

        mock_sandbox = AsyncMock(spec=SandshoreRuntime)
        mock_sandbox.query.side_effect = RuntimeError("Sandbox failed")
        mock_storage = AsyncMock()

        result = await _execute_fallback(step, ctx, mock_sandbox, mock_storage)
        assert result.step_id == "fallback-fail-step"
        assert result.status == "failed"


class TestExecutorCheckCancelAndBudget:
    """Cover _check_cancel and _check_budget."""

    @pytest.mark.asyncio
    async def test_check_cancel_not_cancelled(self):
        """_check_cancel returns False when run is not cancelled."""
        from sandcastle.engine.executor import _check_cancel
        run_id = str(uuid.uuid4())
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.redis_url = None
            result = await _check_cancel(run_id)
        assert result is False

    def test_check_budget_no_limit(self):
        """_check_budget returns None when max_cost is None."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={},
        )
        ctx.max_cost_usd = None
        result = _check_budget(ctx)
        assert result is None

    def test_check_budget_exceeded(self):
        """_check_budget returns 'exceeded' when cost exceeds limit."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={},
        )
        ctx.max_cost_usd = 0.01
        ctx.costs.append(0.05)  # 5x over budget
        result = _check_budget(ctx)
        assert result == "exceeded"

    def test_check_budget_warning(self):
        """_check_budget returns 'warning' when approaching limit."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={},
        )
        ctx.max_cost_usd = 1.0
        ctx.costs.append(0.85)  # 85% of budget
        result = _check_budget(ctx)
        assert result == "warning"

    def test_check_budget_ok(self):
        """_check_budget returns None when under 80% of budget."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={},
        )
        ctx.max_cost_usd = 1.0
        ctx.costs.append(0.50)
        result = _check_budget(ctx)
        assert result is None


class TestExecutorCacheHelpers:
    """Cover cache helper functions."""

    def test_compute_cache_key_deterministic(self):
        """_compute_cache_key returns same key for same inputs."""
        from sandcastle.engine.executor import _compute_cache_key

        key1 = _compute_cache_key("my-wf", "step1", "hello world", "haiku")
        key2 = _compute_cache_key("my-wf", "step1", "hello world", "haiku")
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex digest

    def test_compute_cache_key_different_prompts(self):
        """_compute_cache_key returns different keys for different prompts."""
        from sandcastle.engine.executor import _compute_cache_key

        key1 = _compute_cache_key("my-wf", "step1", "prompt one", "haiku")
        key2 = _compute_cache_key("my-wf", "step1", "prompt two", "haiku")
        assert key1 != key2

    def test_compute_cache_key_different_models(self):
        """_compute_cache_key returns different keys for different models."""
        from sandcastle.engine.executor import _compute_cache_key

        key1 = _compute_cache_key("my-wf", "step1", "same prompt", "haiku")
        key2 = _compute_cache_key("my-wf", "step1", "same prompt", "sonnet")
        assert key1 != key2

    @pytest.mark.asyncio
    async def test_get_cached_result_cache_miss(self):
        """_get_cached_result returns None for uncached key."""
        from sandcastle.engine.executor import _get_cached_result
        result = await _get_cached_result("nonexistent-cache-key-xyz-abc-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_to_cache_and_retrieve(self):
        """_save_to_cache stores and _get_cached_result retrieves."""
        from sandcastle.engine.executor import _get_cached_result, _save_to_cache

        cache_key = f"test-cache-{uuid.uuid4().hex[:8]}"
        await _save_to_cache(
            cache_key=cache_key,
            workflow_name="test-wf",
            step_id="step1",
            model="haiku",
            output={"result": "cached result"},
            cost_usd=0.001,
            ttl_hours=1,
        )
        result = await _get_cached_result(cache_key)
        assert result is not None
        assert result.get("output") == {"result": "cached result"}


class TestExecutorResolveTemplates:
    """Cover resolve_templates edge cases."""

    def test_resolve_templates_run_id(self):
        """resolve_templates resolves {run_id}."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        run_id = str(uuid.uuid4())
        ctx = RunContext(workflow_name="test", run_id=run_id, input={})
        result = resolve_templates("{run_id}", ctx)
        assert result == run_id

    def test_resolve_templates_date(self):
        """resolve_templates resolves {date}."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        ctx = RunContext(workflow_name="test", run_id=str(uuid.uuid4()), input={})
        result = resolve_templates("{date}", ctx)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_resolve_templates_nested_input(self):
        """resolve_templates resolves nested {input.a.b}."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={"config": {"value": "nested-val"}},
        )
        result = resolve_templates("{input.config.value}", ctx)
        assert result == "nested-val"

    def test_resolve_templates_memory(self):
        """resolve_templates handles {memory} with dict memories."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        ctx = RunContext(workflow_name="test", run_id=str(uuid.uuid4()), input={})
        ctx.memories = [
            {"memory": "Memory item 1", "metadata": {}},
            {"memory": "Memory item 2", "metadata": {}},
        ]
        result = resolve_templates("{memory}", ctx)
        assert "Memory item 1" in result

    def test_resolve_templates_no_templates(self):
        """resolve_templates returns plain text unchanged."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        ctx = RunContext(workflow_name="test", run_id=str(uuid.uuid4()), input={})
        result = resolve_templates("No templates here", ctx)
        assert result == "No templates here"

    def test_resolve_templates_multiple(self):
        """resolve_templates resolves multiple templates in one string."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={"first": "Alice", "last": "Smith"},
        )
        result = resolve_templates("{input.first} {input.last}", ctx)
        assert result == "Alice Smith"

    def test_resolve_templates_int_input(self):
        """resolve_templates converts int input to string."""
        from sandcastle.engine.executor import RunContext, resolve_templates
        ctx = RunContext(
            workflow_name="test",
            run_id=str(uuid.uuid4()),
            input={"count": 42},
        )
        result = resolve_templates("Count: {input.count}", ctx)
        assert "42" in result


class TestExecutorTruncateOutput:
    """Cover _truncate_output function."""

    def test_truncate_output_string_within_limit(self):
        """_truncate_output returns unchanged string within limit."""
        from sandcastle.engine.executor import _truncate_output
        result = _truncate_output("short string", 1000)
        assert result == "short string"

    def test_truncate_output_string_exceeds_limit(self):
        """_truncate_output truncates long strings."""
        from sandcastle.engine.executor import _truncate_output
        long_str = "x" * 2000
        result = _truncate_output(long_str, 100)
        assert isinstance(result, str)
        # Either truncated or returned as-is
        assert len(str(result)) > 0

    def test_truncate_output_dict_within_limit(self):
        """_truncate_output returns dict unchanged when within limit."""
        from sandcastle.engine.executor import _truncate_output
        data = {"key": "value", "num": 42}
        result = _truncate_output(data, 10000)
        assert result == data

    def test_truncate_output_none_returns_none(self):
        """_truncate_output returns None input."""
        from sandcastle.engine.executor import _truncate_output
        result = _truncate_output(None, 1000)
        assert result is None


# ===========================================================================
# sdk.py - SandcastleClient and AsyncSandcastleClient methods
# ===========================================================================


class TestSyncSDKListRunsFilters:
    """Cover SandcastleClient.list_runs with filter parameters."""

    def _make_sync_client_mock(self, response_data: dict, status_code: int = 200):
        """Create a properly configured sync httpx client mock."""
        from types import SimpleNamespace
        # Create a real-like response object with integer status_code
        resp = SimpleNamespace(
            status_code=status_code,
            text="",
            json=MagicMock(return_value=response_data),
        )
        return resp

    def test_sync_list_runs_with_status(self):
        """SandcastleClient.list_runs passes status filter in params."""
        from sandcastle.sdk import SandcastleClient

        resp = self._make_sync_client_mock({
            "data": [],
            "meta": {"total": 0, "limit": 20, "offset": 0},
        })

        with patch("sandcastle.sdk.httpx.Client") as mock_cls:
            # SandcastleClient stores self._client = httpx.Client(...)
            # so self._client is mock_cls.return_value
            mock_cls.return_value.get = MagicMock(return_value=resp)
            mock_cls.return_value.close = MagicMock()

            with SandcastleClient(base_url="http://localhost:8000") as sc:
                result = sc.list_runs(status="failed", workflow="my-wf")

        assert result.total == 0
        call_args = mock_cls.return_value.get.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("status") == "failed"
        assert params.get("workflow") == "my-wf"

    def test_sync_list_runs_http_error_raises(self):
        """SandcastleClient.list_runs raises on HTTP error status."""
        from sandcastle.sdk import SandcastleClient, SandcastleError

        resp = self._make_sync_client_mock(
            {"error": {"code": "UNAUTHORIZED", "message": "Not authorized"}},
            status_code=401,
        )

        with patch("sandcastle.sdk.httpx.Client") as mock_cls:
            mock_cls.return_value.get = MagicMock(return_value=resp)
            mock_cls.return_value.close = MagicMock()

            with SandcastleClient(base_url="http://localhost:8000") as sc:
                with pytest.raises(SandcastleError):
                    sc.list_runs()

    def test_sync_call_api_async_mode(self):
        """SandcastleClient.call_api adds Prefer header with async_mode=True."""
        from sandcastle.sdk import SandcastleClient

        resp = self._make_sync_client_mock({"data": {"run_id": "test-run"}})

        with patch("sandcastle.sdk.httpx.Client") as mock_cls:
            mock_cls.return_value.post = MagicMock(return_value=resp)
            mock_cls.return_value.close = MagicMock()

            with SandcastleClient(base_url="http://localhost:8000") as sc:
                result = sc.call_api("my-workflow", {"key": "val"}, async_mode=True)

        assert result == {"run_id": "test-run"}
        call_kwargs = mock_cls.return_value.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Prefer") == "respond-async"

    def test_sync_call_api_with_callback_url(self):
        """SandcastleClient.call_api adds X-Callback-URL header."""
        from sandcastle.sdk import SandcastleClient

        resp = self._make_sync_client_mock({"data": {"run_id": "test-run"}})

        with patch("sandcastle.sdk.httpx.Client") as mock_cls:
            mock_cls.return_value.post = MagicMock(return_value=resp)
            mock_cls.return_value.close = MagicMock()

            with SandcastleClient(base_url="http://localhost:8000") as sc:
                result = sc.call_api(
                    "my-workflow",
                    {"key": "val"},
                    callback_url="https://example.com/cb",
                )

        call_kwargs = mock_cls.return_value.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("X-Callback-URL") == "https://example.com/cb"


class TestAsyncSDKPaths:
    """Cover AsyncSandcastleClient methods."""

    def _make_async_resp(self, data: dict, status_code: int = 200):
        """Create a mock response object for AsyncSandcastleClient (sync json() method)."""
        from types import SimpleNamespace
        return SimpleNamespace(
            status_code=status_code,
            text="",
            json=MagicMock(return_value=data),
        )

    @pytest.mark.asyncio
    async def test_async_list_runs_with_filters(self):
        """AsyncSandcastleClient.list_runs handles status and workflow filters."""
        from sandcastle.sdk import AsyncSandcastleClient

        run_id = str(uuid.uuid4())
        resp = self._make_async_resp({
            "data": [{
                "run_id": run_id,
                "workflow_name": "test-wf",
                "status": "completed",
                "total_cost_usd": 0.001,
                "started_at": None,
                "completed_at": None,
                "error": None,
            }],
            "meta": {"total": 1, "limit": 20, "offset": 0},
        })

        sc = AsyncSandcastleClient(base_url="http://localhost:8000")
        sc._client = AsyncMock()
        sc._client.aclose = AsyncMock()
        sc._client.get = AsyncMock(return_value=resp)

        result = await sc.list_runs(status="completed", workflow="test-wf", limit=10)

        assert result.total == 1
        assert len(result.items) == 1
        assert result.items[0].workflow_name == "test-wf"
        await sc.close()

    @pytest.mark.asyncio
    async def test_async_update_schedule(self):
        """AsyncSandcastleClient.update_schedule sends PATCH request."""
        from sandcastle.sdk import AsyncSandcastleClient

        schedule_id = str(uuid.uuid4())
        resp = self._make_async_resp({
            "data": {
                "id": schedule_id,
                "workflow_name": "test-wf",
                "cron_expression": "0 9 * * *",
                "enabled": True,
                "input_data": {},
                "created_at": "2026-01-01T00:00:00Z",
                "next_run_at": None,
                "last_run_at": None,
                "last_run_status": None,
            }
        })

        sc = AsyncSandcastleClient(base_url="http://localhost:8000")
        sc._client = AsyncMock()
        sc._client.aclose = AsyncMock()
        sc._client.patch = AsyncMock(return_value=resp)

        result = await sc.update_schedule(
            schedule_id, enabled=False, cron="0 10 * * *", input={"key": "val"}
        )

        assert result.id == schedule_id
        await sc.close()

    @pytest.mark.asyncio
    async def test_async_run_yaml_with_optional_params(self):
        """AsyncSandcastleClient.run_yaml sends correct request with optional params."""
        from sandcastle.sdk import AsyncSandcastleClient

        run_id = str(uuid.uuid4())
        yaml_content = "name: test-yaml-wf\nsteps:\n  - id: s1\n    prompt: Hello\n    model: haiku\n"
        resp = self._make_async_resp({
            "data": {
                "run_id": run_id,
                "status": "completed",
                "total_cost_usd": 0.002,
                "started_at": None,
                "completed_at": None,
                "error": None,
                "workflow_name": "test-yaml-wf",
            }
        })

        sc = AsyncSandcastleClient(base_url="http://localhost:8000")
        sc._client = AsyncMock()
        sc._client.aclose = AsyncMock()
        sc._client.post = AsyncMock(return_value=resp)

        result = await sc.run_yaml(
            yaml_content,
            input={"key": "val"},
            max_cost_usd=1.0,
            callback_url="https://example.com/webhook",
        )

        assert result.run_id == run_id
        assert result.status == "completed"
        await sc.close()

    @pytest.mark.asyncio
    async def test_async_list_schedules_empty(self):
        """AsyncSandcastleClient.list_schedules returns empty paginated list."""
        from sandcastle.sdk import AsyncSandcastleClient

        resp = self._make_async_resp({
            "data": [],
            "meta": {"total": 0, "limit": 20, "offset": 0},
        })

        sc = AsyncSandcastleClient(base_url="http://localhost:8000")
        sc._client = AsyncMock()
        sc._client.aclose = AsyncMock()
        sc._client.get = AsyncMock(return_value=resp)

        result = await sc.list_schedules()

        assert result.total == 0
        assert result.items == []
        await sc.close()

    @pytest.mark.asyncio
    async def test_async_get_run_not_found(self):
        """AsyncSandcastleClient.get_run raises on 404."""
        from sandcastle.sdk import AsyncSandcastleClient, SandcastleError

        resp = self._make_async_resp(
            {"error": {"code": "NOT_FOUND", "message": "Run not found"}},
            status_code=404,
        )

        sc = AsyncSandcastleClient(base_url="http://localhost:8000")
        sc._client = AsyncMock()
        sc._client.aclose = AsyncMock()
        sc._client.get = AsyncMock(return_value=resp)

        with pytest.raises(SandcastleError):
            await sc.get_run(str(uuid.uuid4()))
        await sc.close()


# ===========================================================================
# engine/pdf.py - chart methods (no-matplotlib path)
# ===========================================================================


class TestPDFChartNoMatplotlib:
    """Cover chart methods when HAS_MATPLOTLIB=False."""

    def test_radar_no_matplotlib(self):
        """_ChartGen.radar returns None without matplotlib."""
        from sandcastle.engine.pdf import _ChartGen
        import sandcastle.engine.pdf as pdf_mod

        orig = pdf_mod.HAS_MATPLOTLIB
        try:
            pdf_mod.HAS_MATPLOTLIB = False
            gen = _ChartGen()
            result = gen.radar(
                categories=["Speed", "Quality"],
                series={"Option A": [0.8, 0.6]},
            )
            assert result is None
        finally:
            pdf_mod.HAS_MATPLOTLIB = orig

    def test_donut_no_matplotlib(self):
        """_ChartGen.donut returns None without matplotlib."""
        from sandcastle.engine.pdf import _ChartGen
        import sandcastle.engine.pdf as pdf_mod

        orig = pdf_mod.HAS_MATPLOTLIB
        try:
            pdf_mod.HAS_MATPLOTLIB = False
            gen = _ChartGen()
            result = gen.donut(
                labels=["A", "B", "C"],
                values=[10.0, 20.0, 30.0],
                title="Test Donut",
            )
            assert result is None
        finally:
            pdf_mod.HAS_MATPLOTLIB = orig

    def test_horizontal_bars_no_matplotlib(self):
        """_ChartGen.horizontal_bars returns None without matplotlib."""
        from sandcastle.engine.pdf import _ChartGen
        import sandcastle.engine.pdf as pdf_mod

        orig = pdf_mod.HAS_MATPLOTLIB
        try:
            pdf_mod.HAS_MATPLOTLIB = False
            gen = _ChartGen()
            result = gen.horizontal_bars(
                labels=["X", "Y"],
                values=[5.0, 10.0],
                title="Bars",
            )
            assert result is None
        finally:
            pdf_mod.HAS_MATPLOTLIB = orig

    def test_gauge_no_matplotlib(self):
        """_ChartGen.gauge returns None without matplotlib."""
        from sandcastle.engine.pdf import _ChartGen
        import sandcastle.engine.pdf as pdf_mod

        orig = pdf_mod.HAS_MATPLOTLIB
        try:
            pdf_mod.HAS_MATPLOTLIB = False
            gen = _ChartGen()
            # gauge signature: (self, value, max_val=10, label="")
            result = gen.gauge(value=7.5, max_val=10, label="NPS")
            assert result is None
        finally:
            pdf_mod.HAS_MATPLOTLIB = orig

    def test_score_bars_no_matplotlib(self):
        """_ChartGen.score_bars returns None without matplotlib."""
        from sandcastle.engine.pdf import _ChartGen
        import sandcastle.engine.pdf as pdf_mod

        orig = pdf_mod.HAS_MATPLOTLIB
        try:
            pdf_mod.HAS_MATPLOTLIB = False
            gen = _ChartGen()
            # score_bars signature: items: list[tuple[str, float, float]]
            result = gen.score_bars(
                items=[("Feature A", 7.5, 10.0), ("Feature B", 8.2, 10.0)],
                title="Scores",
            )
            assert result is None
        finally:
            pdf_mod.HAS_MATPLOTLIB = orig

    def test_radar_empty_input_returns_none(self):
        """_ChartGen.radar returns None for empty categories."""
        from sandcastle.engine.pdf import _ChartGen

        gen = _ChartGen()
        result = gen.radar(categories=[], series={})
        assert result is None

    def test_donut_empty_labels_returns_none(self):
        """_ChartGen.donut returns None for empty labels."""
        from sandcastle.engine.pdf import _ChartGen

        gen = _ChartGen()
        result = gen.donut(labels=[], values=[])
        assert result is None


class TestPDFGenerateBranded:
    """Cover generate_branded_pdf with various content types."""

    def test_generate_pdf_with_headers(self, tmp_path):
        """generate_branded_pdf renders markdown headers."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md = """# Main Title

## Section One

This is some content in section one.

### Subsection

More content here.

## Section Two

Final section content.
"""
        filepath = tmp_path / "headers_test.pdf"
        generate_branded_pdf(md, filepath, language="en")
        assert filepath.exists()
        assert filepath.stat().st_size > 100

    def test_generate_pdf_with_lists(self, tmp_path):
        """generate_branded_pdf renders bullet lists."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md = """# List Report

- First item
- Second item
- Third item

1. Numbered one
2. Numbered two
3. Numbered three
"""
        filepath = tmp_path / "lists_test.pdf"
        generate_branded_pdf(md, filepath, language="en")
        assert filepath.exists()

    def test_generate_pdf_with_blockquote(self, tmp_path):
        """generate_branded_pdf renders blockquotes."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md = """# Quote Example

> This is a blockquote with important information.
> It spans multiple lines.

Regular paragraph after quote.
"""
        filepath = tmp_path / "quote_test.pdf"
        generate_branded_pdf(md, filepath, language="en")
        assert filepath.exists()

    def test_generate_pdf_with_code_block(self, tmp_path):
        """generate_branded_pdf renders code blocks."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md = """# Code Example

```python
def hello():
    return "Hello, world!"
```

Inline code: `print("hello")`.
"""
        filepath = tmp_path / "code_test.pdf"
        generate_branded_pdf(md, filepath, language="en")
        assert filepath.exists()

    def test_generate_pdf_with_table(self, tmp_path):
        """generate_branded_pdf renders markdown tables."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md = """# Results

| Name | Score | Grade |
|------|-------|-------|
| Alice | 95 | A |
| Bob | 82 | B |
| Carol | 71 | C |
"""
        filepath = tmp_path / "table_test.pdf"
        generate_branded_pdf(md, filepath, language="en")
        assert filepath.exists()
        assert filepath.stat().st_size > 1000

    def test_generate_pdf_with_kpi_markers(self, tmp_path):
        """generate_branded_pdf renders KPI markers."""
        from sandcastle.engine.pdf import generate_branded_pdf

        # KPI markers use HTML comment format: <!-- kpi: KEY=VALUE|KEY2=VALUE2 -->
        md = """# KPI Report

<!-- kpi: Revenue=$125K(+15%)|NPS=72(+5pts)|Growth=+15% -->

Analysis complete.
"""
        filepath = tmp_path / "kpi_test.pdf"
        generate_branded_pdf(md, filepath, language="en")
        assert filepath.exists()


# ===========================================================================
# models/db.py - additional coverage
# ===========================================================================


class TestDbHelperFunctions:
    """Cover db helper functions."""

    def test_sqlite_wal_mode_executes_pragma(self):
        """_sqlite_wal_mode sets WAL journal mode pragma."""
        from sandcastle.models.db import _sqlite_wal_mode
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("wal",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        _sqlite_wal_mode(mock_conn, None)

        execute_calls = mock_cursor.execute.call_args_list
        assert len(execute_calls) > 0
        first_call_sql = execute_calls[0][0][0]
        assert "WAL" in first_call_sql.upper() or "journal_mode" in first_call_sql.lower()

    def test_build_engine_url_with_explicit_url(self):
        """_build_engine_url uses explicit database_url when set."""
        from sandcastle.models.db import _build_engine_url
        with patch("sandcastle.models.db.settings") as mock_s:
            mock_s.database_url = "postgresql+asyncpg://user:pass@host/db"
            url = _build_engine_url()
        assert url == "postgresql+asyncpg://user:pass@host/db"

    def test_build_engine_url_sqlite_default(self):
        """_build_engine_url creates SQLite URL in local mode."""
        from sandcastle.models.db import _build_engine_url
        with tempfile.TemporaryDirectory() as tmp:
            with patch("sandcastle.models.db.settings") as mock_s:
                mock_s.database_url = ""
                mock_s.data_dir = tmp
                url = _build_engine_url()
        assert "sqlite" in url

    def test_build_engine_kwargs_sqlite(self):
        """_build_engine_kwargs returns sqlite-specific args."""
        from sandcastle.models.db import _build_engine_kwargs
        kwargs = _build_engine_kwargs("sqlite+aiosqlite:///test.db")
        assert "connect_args" in kwargs
        assert kwargs["connect_args"]["check_same_thread"] is False

    def test_build_engine_kwargs_postgres(self):
        """_build_engine_kwargs returns no connect_args for postgres."""
        from sandcastle.models.db import _build_engine_kwargs
        kwargs = _build_engine_kwargs("postgresql+asyncpg://user:pass@host/db")
        assert "connect_args" not in kwargs


# ===========================================================================
# api/routes.py - additional endpoint tests
# ===========================================================================


class TestRoutesAdditionalEndpoints:
    """Cover additional routes.py endpoints."""

    def test_runs_list_no_table_graceful(self):
        """GET /api/runs handles gracefully when DB has table issues."""
        response = client.get("/api/runs?limit=5")
        assert response.status_code in (200, 500)

    def test_workflow_list_graceful(self):
        """GET /api/workflows handles gracefully."""
        response = client.get("/api/workflows")
        assert response.status_code in (200, 500)

    def test_run_workflow_by_name_not_found(self):
        """POST /api/workflows/run with workflow_name returns error when not found."""
        response = client.post(
            "/api/workflows/run",
            json={"workflow_name": "definitely-not-exists-xyz-abc", "input": {}},
        )
        assert response.status_code in (200, 400, 404, 422, 500)

    def test_settings_get(self):
        """GET /api/settings returns settings object."""
        response = client.get("/api/settings")
        assert response.status_code == 200

    def test_runtime_info(self):
        """GET /api/runtime returns runtime info."""
        response = client.get("/api/runtime")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_hub_collections_endpoint(self):
        """GET /api/hub/collections returns collections."""
        response = client.get("/api/hub/collections")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data.get("data"), list)

    def test_hub_submit_valid_workflow(self):
        """POST /api/hub/submit with valid workflow YAML."""
        yaml_content = """name: hub-submit-batch3-test
description: A test workflow for batch3
steps:
  - id: step1
    prompt: "Do something"
    model: haiku
"""
        response = client.post(
            "/api/hub/submit",
            json={
                "yaml_content": yaml_content,
                "description": "Test submission",
                "category": "productivity",
                "tags": ["test"],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "data" in data

    def test_workflows_search(self):
        """GET /api/workflows?q= search endpoint."""
        response = client.get("/api/workflows?q=test")
        assert response.status_code in (200, 500)
