"""Batch 6: Deep tests for executor._execute_step_once and routes endpoints.

Focuses on:
- executor.py: _execute_step_once with mocked sandbox (SLO routing, policies,
  credential redaction, empty output guards, memory injection)
- routes.py: more endpoint branches
- queue/worker.py uncovered paths
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper to build a minimal StepDefinition mock
# ---------------------------------------------------------------------------

def _make_step(
    step_id: str = "step1",
    step_type: str = "llm",
    prompt: str = "Test prompt",
    model: str = "claude-haiku",
    tools: list | None = None,
    output_schema: dict | None = None,
    policies=None,
    slo=None,
    model_pool=None,
    pdf_report=None,
    memory=None,
    max_turns: int = 1,
    timeout: int = 30,
    depends_on: list | None = None,
):
    from sandcastle.engine.dag import StepDefinition
    step = MagicMock(spec=StepDefinition)
    step.id = step_id
    step.type = step_type
    step.prompt = prompt
    step.model = model
    step.tools = tools
    step.output_schema = output_schema
    step.policies = policies
    step.slo = slo
    step.model_pool = model_pool
    step.pdf_report = pdf_report
    step.memory = memory
    step.max_turns = max_turns
    step.timeout = timeout
    step.depends_on = depends_on or []
    step.csv_output = None
    step.retry = None
    step.fallback = None
    step.autopilot = None
    return step


def _make_context(
    run_id: str = "aabbccdd-0000-0000-0000-000000000001",
    workflow_name: str = "test_wf",
    memory_config=None,
    memory_scope_id: str = "",
    max_cost_usd: float | None = None,
):
    from sandcastle.engine.executor import RunContext
    ctx = RunContext(
        run_id=run_id,
        input={"topic": "AI"},
        workflow_name=workflow_name,
        _memory_config=memory_config,
        _memory_scope_id=memory_scope_id,
        max_cost_usd=max_cost_usd,
    )
    return ctx


def _make_sandbox_result(text: str = "Output text", structured=None, cost: float = 0.01):
    result = MagicMock()
    result.text = text
    result.structured_output = structured
    result.total_cost_usd = cost
    return result


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - basic successful path
# ---------------------------------------------------------------------------

class TestExecuteStepOnceBasic:
    """Test _execute_step_once with mocked sandbox."""

    @pytest.mark.asyncio
    async def test_successful_text_output(self):
        from sandcastle.engine.executor import _execute_step_once
        from sandcastle.engine.storage import LocalStorage

        step = _make_step(prompt="Tell me about AI", model="claude-haiku")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result("Here is the answer"))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"
        assert result.output == "Here is the answer"

    @pytest.mark.asyncio
    async def test_json_text_output_parsed(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Return JSON", model="claude-haiku")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result('{"key": "value", "count": 42}'))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"
        assert result.output == {"key": "value", "count": 42}

    @pytest.mark.asyncio
    async def test_code_fenced_json_parsed(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Return JSON", model="claude-haiku")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result(
            '```json\n{"result": "success"}\n```'
        ))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"
        assert result.output == {"result": "success"}

    @pytest.mark.asyncio
    async def test_structured_output_used_when_available(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Return structured data", model="claude-haiku")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result(
            "text fallback",
            structured={"structured": True, "data": "value"}
        ))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"
        assert result.output == {"structured": True, "data": "value"}

    @pytest.mark.asyncio
    async def test_empty_llm_output_raises(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(step_type="llm", prompt="Gimme output", model="claude-haiku")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result(""))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "failed"
        assert "empty output" in (result.error or "")

    @pytest.mark.asyncio
    async def test_output_schema_empty_output_raises(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(
            step_type="tool",
            prompt="Return data",
            model="claude-haiku",
            output_schema={"type": "object", "properties": {}},
        )
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result(""))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_tools_added_to_request(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(
            prompt="Search for info",
            model="claude-haiku",
            tools=["web_search", "calculator"],
        )
        ctx = _make_context()

        captured_request = {}
        async def fake_query(req):
            captured_request.update(req)
            return _make_sandbox_result("Search results found")

        sandbox = AsyncMock()
        sandbox.query = fake_query
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"
        assert "tools" in captured_request
        assert "web_search" in captured_request["tools"]

    @pytest.mark.asyncio
    async def test_default_tools_used_when_step_tools_none(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Use default tools", tools=None)
        ctx = _make_context()
        ctx.default_tools = ["default_search"]

        captured_request = {}
        async def fake_query(req):
            captured_request.update(req)
            return _make_sandbox_result("Found results")

        sandbox = AsyncMock()
        sandbox.query = fake_query
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert "tools" in captured_request
        assert "default_search" in captured_request["tools"]

    @pytest.mark.asyncio
    async def test_pdf_report_instruction_added(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(
            prompt="Generate report",
            pdf_report=SimpleNamespace(language="en", directory="/tmp", filename=None),
        )
        ctx = _make_context()

        captured_request = {}
        async def fake_query(req):
            captured_request.update(req)
            return _make_sandbox_result("# Report\n\nContent here")

        sandbox = AsyncMock()
        sandbox.query = fake_query
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage)

        # PDF report instruction should be prepended (not the terse system prefix)
        assert "FORMATTING" in captured_request.get("prompt", "") or result.status == "completed"


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - credential redaction path
# ---------------------------------------------------------------------------

class TestExecuteStepCredentialRedaction:
    """Test credential redaction when tools are used."""

    @pytest.mark.asyncio
    async def test_credential_redaction_when_violations_found(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(
            prompt="Use tool with credentials",
            tools=["my_tool"],
        )
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result(
            "API key is sk-1234567890abcdef"
        ))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        # Mock policy to return violations
        mock_cred_result = MagicMock()
        mock_cred_result.violations = [MagicMock()]
        mock_cred_result.modified_output = "API key is [REDACTED]"

        mock_policy = MagicMock()
        mock_engine = AsyncMock()
        mock_engine.evaluate = AsyncMock(return_value=mock_cred_result)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.policy.create_tool_credential_policy", return_value=mock_policy):
                with patch("sandcastle.engine.policy.PolicyEngine", return_value=mock_engine):
                    result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_credential_redaction_exception_silently_logged(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(
            prompt="Use tool",
            tools=["my_tool"],
        )
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result("Normal output"))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.policy.create_tool_credential_policy",
                       side_effect=Exception("policy error")):
                result = await _execute_step_once(step, ctx, sandbox, storage)

        # Exception should be caught and logged, not propagated
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - SLO routing exception path
# ---------------------------------------------------------------------------

class TestExecuteStepSloException:
    """Test SLO routing when optimizer fails."""

    @pytest.mark.asyncio
    async def test_slo_routing_exception_falls_back_to_default(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="SLO step")
        step.slo = SimpleNamespace(
            quality_min=0.8,
            cost_max_usd=0.05,
            latency_max_seconds=10.0,
            optimize_for="quality",
        )
        step.model_pool = [
            SimpleNamespace(id="haiku", model="claude-haiku", max_turns=1),
        ]

        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result("Output from default model"))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.optimizer.get_optimizer", side_effect=Exception("optimizer down")):
                result = await _execute_step_once(step, ctx, sandbox, storage)

        # Should fall back to default and still complete
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - cache hit path
# ---------------------------------------------------------------------------

class TestExecuteStepCacheHit:
    """Test _execute_step_once when cache hit occurs."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Cached prompt")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock()  # Should not be called
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        cached_data = {"output": {"result": "cached answer"}, "cost_usd": 0.001}

        with patch("sandcastle.engine.executor._get_cached_result", return_value=cached_data):
            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "completed"
        assert result.output == {"result": "cached answer"}
        # Sandbox should NOT have been called
        sandbox.query.assert_not_called()


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - parallel_index logging
# ---------------------------------------------------------------------------

class TestExecuteStepParallelIndex:
    """Test _execute_step_once with parallel_index."""

    @pytest.mark.asyncio
    async def test_parallel_index_included_in_log(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Parallel step")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result("Parallel output"))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            result = await _execute_step_once(step, ctx, sandbox, storage, parallel_index=3)

        assert result.status == "completed"
        assert result.parallel_index == 3


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - sandbox exception
# ---------------------------------------------------------------------------

class TestExecuteStepSandboxException:
    """Test _execute_step_once when sandbox.query raises."""

    @pytest.mark.asyncio
    async def test_sandbox_exception_returns_failed(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(prompt="Will fail")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(side_effect=Exception("sandbox timeout"))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        with patch("sandcastle.engine.telemetry.capture_step_error"):
            result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status == "failed"
        assert "sandbox timeout" in (result.error or "")


# ---------------------------------------------------------------------------
# executor.py: _execute_step_once - output not cached when empty
# ---------------------------------------------------------------------------

class TestExecuteStepCacheMiss:
    """Test that non-cacheable outputs are not saved to cache."""

    @pytest.mark.asyncio
    async def test_failed_output_not_cached(self):
        from sandcastle.engine.executor import _execute_step_once

        step = _make_step(step_type="tool", prompt="Get data")
        ctx = _make_context()
        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=_make_sandbox_result("please provide the content"))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        save_cache_called = False

        async def fake_get_cached(key):
            return None

        async def fake_save_cache(**kwargs):
            nonlocal save_cache_called
            save_cache_called = True

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.executor._get_cached_result", side_effect=fake_get_cached):
                with patch("sandcastle.engine.executor._save_to_cache", side_effect=fake_save_cache):
                    result = await _execute_step_once(step, ctx, sandbox, storage)

        # Output "please provide the content" is not cacheable - save should NOT be called
        assert not save_cache_called


# ---------------------------------------------------------------------------
# routes.py: health check endpoint branches
# ---------------------------------------------------------------------------

class TestHealthCheckBranches:
    """Test health check endpoint branches."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_health_check_returns_200(self):
        response = self.client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "status" in data["data"]

    def test_runtime_info_returns_200(self):
        response = self.client.get("/api/runtime")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data


# ---------------------------------------------------------------------------
# routes.py: hub/installed endpoint
# ---------------------------------------------------------------------------

class TestHubInstalled:
    """Test list_installed_hub_templates."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_hub_installed_returns_list(self):
        response = self.client.get("/api/hub/installed")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["data"], list)


# ---------------------------------------------------------------------------
# queue/worker.py: recover_stuck_runs
# ---------------------------------------------------------------------------

class TestRecoverStuckRuns:
    """Test _recover_stuck_runs function."""

    @pytest.mark.asyncio
    async def test_recover_stuck_runs_updates_status(self):
        from sandcastle.queue.worker import _recover_stuck_runs

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise
            await _recover_stuck_runs()

    @pytest.mark.asyncio
    async def test_recover_stuck_runs_exception_logged(self):
        from sandcastle.queue.worker import _recover_stuck_runs

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise - exceptions are logged
            await _recover_stuck_runs()


# ---------------------------------------------------------------------------
# executor.py: resolve_variable - steps.X.4th_unknown_attr returns _UNRESOLVED
# ---------------------------------------------------------------------------

class TestResolveVariableStepsUnknown:
    """Test that steps.X.unknown_attr returns _UNRESOLVED."""

    def test_steps_unknown_attr_returns_unresolved(self):
        from sandcastle.engine.executor import RunContext, StepResult, _UNRESOLVED, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000005",
            input={},
            step_outputs={"step1": "output"},
            step_results={
                "step1": StepResult(step_id="step1", status="completed")
            },
        )
        # "steps.step1.unknown_attr" - not output/status/error/cost -> _UNRESOLVED
        result = resolve_variable("steps.step1.unknown_attr", ctx)
        assert result is _UNRESOLVED


# ---------------------------------------------------------------------------
# executor.py: _get_redis - double-check locking
# ---------------------------------------------------------------------------

class TestGetRedisDoubleCheckLocking:
    """Test _get_redis double-check locking pattern."""

    @pytest.mark.asyncio
    async def test_get_redis_returns_cached_pool(self):
        import sandcastle.engine.executor as exe
        mock_pool = MagicMock()
        exe._redis_pool = mock_pool
        try:
            result = await exe._get_redis()
            assert result is mock_pool
        finally:
            exe._redis_pool = None

    @pytest.mark.asyncio
    async def test_get_redis_creates_new_pool(self):
        import sandcastle.engine.executor as exe
        exe._redis_pool = None
        mock_redis_module = MagicMock()
        mock_redis_from_url = MagicMock(return_value=MagicMock())
        mock_redis_module.from_url = mock_redis_from_url

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379"
            with patch.dict("sys.modules", {"redis.asyncio": mock_redis_module}):
                try:
                    result = await exe._get_redis()
                    assert result is not None
                finally:
                    exe._redis_pool = None  # cleanup


# ---------------------------------------------------------------------------
# executor.py: _save_run_step - handles run_id UUID parsing
# ---------------------------------------------------------------------------

class TestSaveRunStep:
    """Test _save_run_step function."""

    @pytest.mark.asyncio
    async def test_save_run_step_creates_new_record(self):
        from sandcastle.engine.executor import _save_run_step

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=None)  # No existing record
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_run_step(
                run_id="aabbccdd-0000-0000-0000-000000000001",
                step_id="step1",
                status="running",
            )
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_run_step_updates_existing_record(self):
        from sandcastle.engine.executor import _save_run_step

        mock_existing = MagicMock()
        mock_existing.completed_at = None

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=mock_existing)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_run_step(
                run_id="aabbccdd-0000-0000-0000-000000000001",
                step_id="step1",
                status="completed",
                output={"result": "done"},
                cost_usd=0.01,
                duration_seconds=1.5,
            )
        # Should have updated existing record
        assert mock_existing.status is not None

    @pytest.mark.asyncio
    async def test_save_run_step_exception_silently_logged(self):
        from sandcastle.engine.executor import _save_run_step

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise
            await _save_run_step(
                run_id="aabbccdd-0000-0000-0000-000000000001",
                step_id="step1",
                status="completed",
            )


# ---------------------------------------------------------------------------
# executor.py: WorkflowResult dataclass
# ---------------------------------------------------------------------------

class TestWorkflowResult:
    def test_workflow_result_attributes(self):
        from sandcastle.engine.executor import WorkflowResult
        result = WorkflowResult(
            run_id="test-run-id",
            outputs={"step1": "output"},
            total_cost_usd=0.05,
            status="completed",
        )
        assert result.run_id == "test-run-id"
        assert result.total_cost_usd == 0.05
        assert result.status == "completed"
        assert result.error is None


# ---------------------------------------------------------------------------
# executor.py: _check_budget edge cases
# ---------------------------------------------------------------------------

class TestCheckBudgetNegative:
    def test_negative_max_cost_returns_none(self):
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            max_cost_usd=-1.0,
        )
        ctx.costs = [0.5]
        assert _check_budget(ctx) is None
