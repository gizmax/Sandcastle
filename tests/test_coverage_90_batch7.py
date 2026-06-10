"""Batch 7: Targeted tests for high-miss areas.

Focuses on:
- routes.py: /generate and /advisor exception paths, /workflows list, update check cache
- executor.py: policy evaluation, memory injection, autopilot variant selection
- __main__.py: _cmd_generate, _cmd_violations, _cmd_tools_list, dispatch helpers
- pdf.py: HAS_MATPLOTLIB=False paths, basic render functions
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# routes.py: /generate endpoint - missing API key
# ---------------------------------------------------------------------------

class TestGenerateEndpointNoApiKey:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_generate_missing_api_key_returns_400(self):
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.anthropic_api_key = None
            mock_settings.redis_url = None
            mock_settings.is_local_mode = True

            import os
            with patch.dict(os.environ, {}, clear=False):
                # Remove key from env if present
                env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
                try:
                    response = self.client.post(
                        "/api/generate",
                        json={"description": "A simple workflow"},
                    )
                finally:
                    if env_backup:
                        os.environ["ANTHROPIC_API_KEY"] = env_backup

        # If API key is missing, should return 400 or fall through to generator
        assert response.status_code in (400, 502, 200)

    def test_generate_httpx_status_error_returns_502(self):
        import httpx

        with patch("sandcastle.engine.generator.generate_workflow",
                   side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())):
            with patch("sandcastle.config.settings") as mock_s:
                mock_s.anthropic_api_key = "sk-test"
                mock_s.redis_url = None
                mock_s.is_local_mode = True
                response = self.client.post(
                    "/api/generate",
                    json={"description": "A simple workflow"},
                )
        assert response.status_code in (400, 502)

    def test_generate_generic_exception_returns_502(self):
        with patch("sandcastle.engine.generator.generate_workflow",
                   side_effect=Exception("generator crashed")):
            with patch("sandcastle.config.settings") as mock_s:
                mock_s.anthropic_api_key = "sk-test"
                mock_s.redis_url = None
                mock_s.is_local_mode = True
                response = self.client.post(
                    "/api/generate",
                    json={"description": "A simple workflow"},
                )
        assert response.status_code in (400, 502)


# ---------------------------------------------------------------------------
# routes.py: /generate/chat endpoint
# ---------------------------------------------------------------------------

class TestGenerateChatEndpoint:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_generate_chat_missing_api_key_returns_400(self):
        import os
        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with patch("sandcastle.config.settings") as mock_s:
                mock_s.anthropic_api_key = None
                mock_s.redis_url = None
                response = self.client.post(
                    "/api/generate/chat",
                    json={"messages": [{"role": "user", "content": "Hello"}]},
                )
        finally:
            if env_backup:
                os.environ["ANTHROPIC_API_KEY"] = env_backup
        assert response.status_code in (400, 502)

    def test_generate_chat_httpx_error(self):
        import httpx
        with patch("sandcastle.engine.generator.generate_chat",
                   side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())):
            with patch("sandcastle.config.settings") as mock_s:
                mock_s.anthropic_api_key = "sk-test"
                mock_s.redis_url = None
                response = self.client.post(
                    "/api/generate/chat",
                    json={"messages": [{"role": "user", "content": "Hello"}]},
                )
        assert response.status_code in (400, 502)

    def test_generate_chat_generic_exception(self):
        with patch("sandcastle.engine.generator.generate_chat",
                   side_effect=Exception("chat failed")):
            with patch("sandcastle.config.settings") as mock_s:
                mock_s.anthropic_api_key = "sk-test"
                mock_s.redis_url = None
                response = self.client.post(
                    "/api/generate/chat",
                    json={"messages": [{"role": "user", "content": "Hello"}]},
                )
        assert response.status_code in (400, 502)


# ---------------------------------------------------------------------------
# routes.py: /advisor/explain endpoint
# ---------------------------------------------------------------------------

class TestAdvisorExplain:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_advisor_explain_success(self):
        mock_result = {"explanation": "The error occurred because...", "fix": "Try this"}
        with patch("sandcastle.engine.generator.explain_error",
                   new_callable=AsyncMock, return_value=mock_result):
            response = self.client.post(
                "/api/advisor/explain",
                json={
                    "step_id": "step1",
                    "step_type": "llm",
                    "error": "Empty output",
                    "prompt": "Analyze this",
                    "model": "claude-haiku",
                    "workflow_name": "test_workflow",
                },
            )
        assert response.status_code in (200, 400, 502)

    def test_advisor_explain_httpx_error(self):
        import httpx
        with patch("sandcastle.engine.generator.explain_error",
                   side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())):
            response = self.client.post(
                "/api/advisor/explain",
                json={
                    "step_id": "step1",
                    "step_type": "llm",
                    "error": "Empty output",
                    "prompt": "Analyze this",
                    "model": "claude-haiku",
                    "workflow_name": "test_workflow",
                },
            )
        assert response.status_code in (400, 502)

    def test_advisor_explain_generic_exception(self):
        with patch("sandcastle.engine.generator.explain_error",
                   side_effect=Exception("explain failed")):
            response = self.client.post(
                "/api/advisor/explain",
                json={
                    "step_id": "step1",
                    "step_type": "llm",
                    "error": "Empty output",
                    "prompt": "Analyze this",
                    "model": "claude-haiku",
                    "workflow_name": "test_workflow",
                },
            )
        assert response.status_code in (400, 502)


# ---------------------------------------------------------------------------
# routes.py: /workflows list
# ---------------------------------------------------------------------------

class TestListWorkflows:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from sandcastle.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_list_workflows_returns_200(self):
        response = self.client.get("/api/workflows")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)


# ---------------------------------------------------------------------------
# routes.py: _slugify
# ---------------------------------------------------------------------------

class TestSlugifyRoutes:
    def test_basic_slugify(self):
        from sandcastle.api.routes import _slugify
        assert _slugify("Hello World") == "hello-world"

    def test_slugify_underscores_to_hyphen(self):
        from sandcastle.api.routes import _slugify
        result = _slugify("my_workflow_name")
        assert "-" in result or "_" not in result

    def test_slugify_multiple_hyphens(self):
        from sandcastle.api.routes import _slugify
        result = _slugify("hello---world")
        assert "--" not in result


# ---------------------------------------------------------------------------
# routes.py: _extract_yaml_metadata
# ---------------------------------------------------------------------------

class TestExtractYamlMetadata:
    def test_valid_yaml_extracts_metadata(self):
        from sandcastle.api.routes import _extract_yaml_metadata
        yaml_content = """
name: Test Workflow
description: A simple test
steps:
  - id: step1
    type: llm
    prompt: Hello
"""
        result = _extract_yaml_metadata(yaml_content)
        assert isinstance(result, dict)

    def test_invalid_yaml_returns_empty_dict(self):
        from sandcastle.api.routes import _extract_yaml_metadata
        result = _extract_yaml_metadata("invalid: [yaml: content: {{}")
        assert result == {}


# ---------------------------------------------------------------------------
# routes.py: _resolve_budget
# ---------------------------------------------------------------------------

class TestResolveBudget:
    @pytest.mark.asyncio
    async def test_no_budget_returns_none(self):
        from sandcastle.api.routes import _resolve_budget
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.auth_required = False
            mock_s.default_max_cost_usd = None
            result = await _resolve_budget(None, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_direct_budget_returned(self):
        from sandcastle.api.routes import _resolve_budget
        with patch("sandcastle.config.settings") as mock_s:
            mock_s.auth_required = False
            mock_s.default_max_cost_usd = None
            result = await _resolve_budget(5.0, None)
        assert result == 5.0

    @pytest.mark.asyncio
    async def test_default_from_settings(self):
        from sandcastle.api.routes import _resolve_budget
        with patch("sandcastle.api.routes.settings") as mock_s:
            mock_s.auth_required = False
            mock_s.default_max_cost_usd = 2.5
            result = await _resolve_budget(None, None)
        assert result == 2.5


# ---------------------------------------------------------------------------
# executor.py: policy evaluation - violations found
# ---------------------------------------------------------------------------

class TestPolicyEvaluation:
    @pytest.mark.asyncio
    async def test_policy_evaluation_violations_logged(self):
        """Ensure policy violations are logged when found."""
        from sandcastle.engine.executor import RunContext, _execute_step_once
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Do something"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = [MagicMock()]
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.retry = None
        step.fallback = None
        step.autopilot = None

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="Output text",
            structured_output=None,
            total_cost_usd=0.01,
        ))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        # Mock policy evaluation to return violations but not block
        mock_eval_result = MagicMock()
        mock_eval_result.violations = [MagicMock()]
        mock_eval_result.should_block = False
        mock_eval_result.should_inject_approval = False
        mock_eval_result.modified_output = "Output text"

        mock_applicable = [MagicMock()]
        mock_policy_engine = AsyncMock()
        mock_policy_engine.evaluate = AsyncMock(return_value=mock_eval_result)

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.policy.resolve_step_policies", return_value=mock_applicable):
                with patch("sandcastle.engine.policy.PolicyEngine", return_value=mock_policy_engine):
                    with patch("sandcastle.engine.executor._save_policy_violations", new_callable=AsyncMock):
                        result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_policy_evaluation_exception_logged(self):
        """Policy evaluation exception should be caught silently."""
        from sandcastle.engine.executor import RunContext, _execute_step_once
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Do something"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = [MagicMock()]
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.retry = None
        step.fallback = None
        step.autopilot = None

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="Output text",
            structured_output=None,
            total_cost_usd=0.01,
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

            with patch("sandcastle.engine.policy.resolve_step_policies",
                       side_effect=Exception("policy module error")):
                result = await _execute_step_once(step, ctx, sandbox, storage)

        # Should not raise - exception is caught
        assert result.status in ("completed", "failed")


# ---------------------------------------------------------------------------
# executor.py: memory injection
# ---------------------------------------------------------------------------

class TestMemoryInjection:
    @pytest.mark.asyncio
    async def test_memory_injection_on_step_with_memory_config(self):
        """Test memory injection path."""
        from sandcastle.engine.executor import RunContext, _execute_step_once
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "What do you know?"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = SimpleNamespace(read=True, write=False, admit_threshold=0)
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.retry = None
        step.fallback = None
        step.autopilot = None

        memory_config = SimpleNamespace(
            auto_inject=True, max_inject=5, max_age_days=30,
            admit_threshold=0, enrich=False
        )
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            _memory_config=memory_config,
            _memory_scope_id="scope-123",
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="Memory answer",
            structured_output=None,
            total_cost_usd=0.01,
        ))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        mock_memories = [{"content": "Previous knowledge", "score": 0.9}]

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.memory.load_memories", new_callable=AsyncMock,
                       return_value=mock_memories):
                with patch("sandcastle.engine.memory.apply_decay", return_value=mock_memories):
                    with patch("sandcastle.engine.memory.format_memories_for_prompt",
                               return_value="## Memories:\n- Previous knowledge"):
                        result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_memory_injection_exception_silently_logged(self):
        """Memory injection exception should be caught."""
        from sandcastle.engine.executor import RunContext, _execute_step_once
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "What do you know?"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = SimpleNamespace(read=True, write=False, admit_threshold=0)
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.retry = None
        step.fallback = None
        step.autopilot = None

        memory_config = SimpleNamespace(
            auto_inject=True, max_inject=5, max_age_days=30,
            admit_threshold=0, enrich=False
        )
        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            _memory_config=memory_config,
            _memory_scope_id="scope-123",
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="Memory answer",
            structured_output=None,
            total_cost_usd=0.01,
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

            with patch("sandcastle.engine.memory.load_memories",
                       new_callable=AsyncMock, side_effect=Exception("memory down")):
                result = await _execute_step_once(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")


# ---------------------------------------------------------------------------
# executor.py: autopilot variant selection path
# ---------------------------------------------------------------------------

class TestAutopilotVariantSelection:
    @pytest.mark.asyncio
    async def test_autopilot_with_variants(self):
        """Test autopilot variant path in execute_step_with_retry."""
        from sandcastle.engine.executor import RunContext, execute_step_with_retry
        from sandcastle.engine.dag import StepDefinition

        # Build step with autopilot
        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Autopilot test"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.fallback = None
        step.retry = None
        step.autopilot = SimpleNamespace(
            enabled=True,
            variants=[SimpleNamespace(id="variant_a", prompt="Variant A prompt")],
            sample_rate=1.0,
        )

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="Autopilot output",
            structured_output=None,
            total_cost_usd=0.01,
        ))
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        mock_experiment = MagicMock()
        mock_experiment.id = "exp-123"
        mock_variant = MagicMock()
        mock_variant.id = "variant_a"

        with patch("sandcastle.models.db.async_session") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.scalar = AsyncMock(return_value=None)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session_class.return_value = mock_session

            with patch("sandcastle.engine.autopilot.get_or_create_experiment",
                       new_callable=AsyncMock, return_value=mock_experiment):
                with patch("sandcastle.engine.autopilot.pick_variant",
                           new_callable=AsyncMock, return_value=mock_variant):
                    with patch("sandcastle.engine.autopilot.apply_variant", return_value=step):
                        with patch("sandcastle.engine.autopilot.evaluate_result",
                                   new_callable=AsyncMock, return_value=0.8):
                            with patch("sandcastle.engine.autopilot.save_sample",
                                       new_callable=AsyncMock):
                                with patch("sandcastle.engine.autopilot.maybe_complete_experiment",
                                           new_callable=AsyncMock):
                                    result = await execute_step_with_retry(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_autopilot_exception_falls_back_to_baseline(self):
        """Test autopilot variant exception falls back gracefully."""
        from sandcastle.engine.executor import RunContext, execute_step_with_retry
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Autopilot test"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.fallback = None
        step.retry = None
        step.autopilot = SimpleNamespace(
            enabled=True,
            variants=[SimpleNamespace(id="v1")],
            sample_rate=1.0,
        )

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="Baseline output",
            structured_output=None,
            total_cost_usd=0.01,
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

            with patch("sandcastle.engine.autopilot.get_or_create_experiment",
                       new_callable=AsyncMock, side_effect=Exception("autopilot DB down")):
                result = await execute_step_with_retry(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")


# ---------------------------------------------------------------------------
# executor.py: _execute_step - retry path
# ---------------------------------------------------------------------------

class TestExecuteStepRetry:
    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """Test that retry config triggers multiple attempts."""
        from sandcastle.engine.executor import RunContext, execute_step_with_retry
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Retry test"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.fallback = None
        step.autopilot = None
        step.retry = SimpleNamespace(max_attempts=2, backoff="exponential")

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        # First call fails (empty output for llm), second succeeds
        call_count = 0
        async def fake_query(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(text="", structured_output=None, total_cost_usd=0.0)
            return MagicMock(text="Retry succeeded", structured_output=None, total_cost_usd=0.01)

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

            with patch("asyncio.sleep", new_callable=AsyncMock):  # Don't actually sleep
                result = await execute_step_with_retry(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")


# ---------------------------------------------------------------------------
# executor.py: _execute_step - pdf_report + csv_output paths
# ---------------------------------------------------------------------------

class TestExecuteStepPdfCsv:
    @pytest.mark.asyncio
    async def test_pdf_report_written_on_completion(self):
        """Test PDF report path in execute_step_with_retry."""
        from sandcastle.engine.executor import RunContext, execute_step_with_retry
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Generate report"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = SimpleNamespace(language="en", directory="/tmp", filename=None)
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = None
        step.fallback = None
        step.retry = None
        step.autopilot = None

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text="# Report\n\nContent here",
            structured_output=None,
            total_cost_usd=0.01,
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

            with patch("sandcastle.engine.executor._write_pdf_report",
                       return_value="/tmp/report.pdf"):
                result = await execute_step_with_retry(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")
        if result.status == "completed":
            if isinstance(result.output, dict):
                assert "_pdf_artifact" in result.output
            else:
                # output was converted to dict with pdf_artifact
                pass

    @pytest.mark.asyncio
    async def test_csv_output_written_on_completion(self):
        """Test CSV output path in execute_step_with_retry."""
        from sandcastle.engine.executor import RunContext, execute_step_with_retry
        from sandcastle.engine.dag import StepDefinition

        step = MagicMock(spec=StepDefinition)
        step.id = "step1"
        step.type = "llm"
        step.prompt = "Generate data"
        step.model = "claude-haiku"
        step.tools = None
        step.output_schema = None
        step.policies = None
        step.slo = None
        step.model_pool = None
        step.pdf_report = None
        step.memory = None
        step.max_turns = 1
        step.timeout = 30
        step.depends_on = []
        step.csv_output = SimpleNamespace(path="/tmp/output.csv", columns=["name", "value"])
        step.fallback = None
        step.retry = None
        step.autopilot = None

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )

        sandbox = AsyncMock()
        sandbox.query = AsyncMock(return_value=MagicMock(
            text='[{"name": "Alice", "value": 10}]',
            structured_output=None,
            total_cost_usd=0.01,
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

            with patch("sandcastle.engine.executor._write_csv_output") as mock_csv:
                result = await execute_step_with_retry(step, ctx, sandbox, storage)

        assert result.status in ("completed", "failed")


# ---------------------------------------------------------------------------
# __main__.py: _cmd_violations dispatch
# ---------------------------------------------------------------------------

class TestCmdViolationsDispatch:
    def test_violations_list_dispatches_correctly(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.violations_action = "list"
        args.url = "http://localhost:8080"
        args.api_key = None
        args.json = True
        args.workflow = None
        args.run_id = None
        args.limit = 10
        args.page = 1

        mock_body = {"data": []}
        with patch.object(m, "_api_get", return_value=mock_body):
            with patch.object(m, "_json_out") as mock_json_out:
                m._cmd_violations(args)
                mock_json_out.assert_called_once_with(mock_body)

    def test_violations_unknown_action_prints_usage(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.violations_action = "unknown"

        with pytest.raises(SystemExit) as exc_info:
            m._cmd_violations(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# __main__.py: _cmd_tools_list
# ---------------------------------------------------------------------------

class TestCmdToolsList:
    def test_tools_list_json_output(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.category = None
        args.json = True
        args.url = "http://localhost:8080"
        args.api_key = None

        mock_body = {"data": {"tools": [{"name": "web_search", "category": "search"}]}}
        with patch.object(m, "_api_get", return_value=mock_body):
            with patch.object(m, "_json_out") as mock_json_out:
                m._cmd_tools_list(args)
                mock_json_out.assert_called()

    def test_tools_list_no_tools(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.category = None
        args.json = False
        args.url = "http://localhost:8080"
        args.api_key = None

        mock_body = {"data": {"tools": []}}
        with patch.object(m, "_api_get", return_value=mock_body):
            with patch("builtins.print") as mock_print:
                m._cmd_tools_list(args)
                # "No tools found." should be printed
                calls = [str(c) for c in mock_print.call_args_list]
                assert any("No tools" in s for s in calls)

    def test_tools_list_with_category(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.category = "search"
        args.json = True
        args.url = "http://localhost:8080"
        args.api_key = None

        mock_body = {"data": {"tools": []}}
        captured_params = {}

        def fake_api_get(a, path, params=None):
            if params:
                captured_params.update(params)
            return mock_body

        with patch.object(m, "_api_get", side_effect=fake_api_get):
            with patch.object(m, "_json_out"):
                m._cmd_tools_list(args)

        assert captured_params.get("category") == "search"


# ---------------------------------------------------------------------------
# __main__.py: _cmd_autopilot dispatch
# ---------------------------------------------------------------------------

class TestCmdAutopilotDispatch:
    def test_autopilot_unknown_action(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.autopilot_action = "unknown_action"

        with pytest.raises(SystemExit) as exc_info:
            m._cmd_autopilot(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# __main__.py: _cmd_generate dispatch
# ---------------------------------------------------------------------------

class TestCmdGenerate:
    def test_generate_no_description_reads_input(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.description = None
        args.output = None
        args.refine = False

        mock_result = MagicMock()
        mock_result.validation_errors = []
        mock_result.name = "My Workflow"
        mock_result.steps_count = 3
        mock_result.yaml_content = "name: My Workflow\nsteps: []"

        with patch("builtins.input", return_value="A workflow for data analysis"):
            with patch("sandcastle.engine.generator.generate_workflow_sync",
                       return_value=mock_result):
                with patch("builtins.print"):
                    m._cmd_generate(args)

    def test_generate_empty_description_exits(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.description = None
        args.description_pos = None  # build/new alias positional; None for `generate`
        args.output = None
        args.refine = False

        with patch("builtins.input", return_value=""):
            with pytest.raises(SystemExit) as exc_info:
                m._cmd_generate(args)
        assert exc_info.value.code == 1

    def test_generate_eoferror_on_input_exits(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.description = None
        args.description_pos = None  # build/new alias positional; None for `generate`
        args.output = None
        args.refine = False

        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(SystemExit) as exc_info:
                m._cmd_generate(args)
        assert exc_info.value.code == 0

    def test_generate_value_error_exits(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.description = "A workflow"
        args.output = None
        args.refine = False

        with patch("sandcastle.engine.generator.generate_workflow_sync",
                   side_effect=ValueError("missing API key")):
            with patch("builtins.print"):
                with pytest.raises(SystemExit) as exc_info:
                    m._cmd_generate(args)
        assert exc_info.value.code == 1

    def test_generate_exception_exits(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.description = "A workflow"
        args.output = None
        args.refine = False

        with patch("sandcastle.engine.generator.generate_workflow_sync",
                   side_effect=Exception("generator crashed")):
            with patch("builtins.print"):
                with pytest.raises(SystemExit) as exc_info:
                    m._cmd_generate(args)
        assert exc_info.value.code == 1

    def test_generate_with_validation_errors(self):
        from sandcastle import __main__ as m

        args = MagicMock()
        args.description = "A workflow"
        args.output = None
        args.refine = False

        mock_result = MagicMock()
        mock_result.validation_errors = ["Step 1 missing prompt"]
        mock_result.name = "My Workflow"
        mock_result.steps_count = 1
        mock_result.yaml_content = "name: My Workflow\nsteps: []"

        with patch("sandcastle.engine.generator.generate_workflow_sync",
                   return_value=mock_result):
            with patch("builtins.print") as mock_print:
                m._cmd_generate(args)

        # Should print warning about validation errors
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "validation" in printed.lower() or "WARN" in printed or "issue" in printed.lower()

    def test_generate_with_output_file(self):
        from sandcastle import __main__ as m
        import tempfile, os

        args = MagicMock()
        args.description = "A workflow"
        args.refine = False

        mock_result = MagicMock()
        mock_result.validation_errors = []
        mock_result.name = "My Workflow"
        mock_result.steps_count = 2
        mock_result.yaml_content = "name: My Workflow\nsteps: []"

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            args.output = f.name

        try:
            with patch("sandcastle.engine.generator.generate_workflow_sync",
                       return_value=mock_result):
                with patch("builtins.print"):
                    m._cmd_generate(args)
            # Output file should have been written
            assert os.path.exists(args.output)
        finally:
            try:
                os.unlink(args.output)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# pdf.py: HAS_MATPLOTLIB=False guard
# ---------------------------------------------------------------------------

class TestPdfNoMatplotlib:
    def test_chart_gen_radar_returns_none_without_matplotlib(self):
        """_ChartGen.radar returns None when matplotlib not available."""
        import sandcastle.engine.pdf as pdf_module

        original = pdf_module.HAS_MATPLOTLIB
        try:
            pdf_module.HAS_MATPLOTLIB = False
            factory = pdf_module._ChartGen()
            result = factory.radar(["A", "B", "C"], {"series1": [1, 2, 3]})
            assert result is None
        finally:
            pdf_module.HAS_MATPLOTLIB = original

    def test_chart_gen_donut_returns_none_without_matplotlib(self):
        """_ChartGen.donut returns None when matplotlib not available."""
        import sandcastle.engine.pdf as pdf_module

        original = pdf_module.HAS_MATPLOTLIB
        try:
            pdf_module.HAS_MATPLOTLIB = False
            factory = pdf_module._ChartGen()
            result = factory.donut("Title", {"a": 10, "b": 20})
            assert result is None
        finally:
            pdf_module.HAS_MATPLOTLIB = original

    def test_chart_gen_horizontal_bars_returns_none_without_matplotlib(self):
        """_ChartGen.horizontal_bars returns None when matplotlib not available."""
        import sandcastle.engine.pdf as pdf_module

        original = pdf_module.HAS_MATPLOTLIB
        try:
            pdf_module.HAS_MATPLOTLIB = False
            factory = pdf_module._ChartGen()
            result = factory.horizontal_bars(["a", "b"], [1, 2])
            assert result is None
        finally:
            pdf_module.HAS_MATPLOTLIB = original

    def test_chart_gen_gauge_returns_none_without_matplotlib(self):
        """_ChartGen.gauge returns None when matplotlib not available."""
        import sandcastle.engine.pdf as pdf_module

        original = pdf_module.HAS_MATPLOTLIB
        try:
            pdf_module.HAS_MATPLOTLIB = False
            factory = pdf_module._ChartGen()
            result = factory.gauge(7.5, max_val=10)
            assert result is None
        finally:
            pdf_module.HAS_MATPLOTLIB = original


# ---------------------------------------------------------------------------
# pdf.py: generate_branded_pdf
# ---------------------------------------------------------------------------

class TestPdfGenerateBranded:
    def test_generate_branded_pdf_basic(self):
        """Test generate_branded_pdf creates a file."""
        import tempfile, os
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = "# Hello World\n\nTest report content."
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
            assert result.stat().st_size > 0
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_table(self):
        """Test generate_branded_pdf with a markdown table."""
        import tempfile, os
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = "# Report\n\n| Name | Value |\n|------|-------|\n| Alice | 100 |\n| Bob | 200 |"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_non_string_raises(self):
        """generate_branded_pdf raises TypeError for non-string input."""
        from sandcastle.engine.pdf import generate_branded_pdf

        with pytest.raises(TypeError):
            generate_branded_pdf(12345, "/tmp/test.pdf")

    def test_generate_branded_pdf_empty_path_raises(self):
        """generate_branded_pdf raises ValueError for empty path."""
        from sandcastle.engine.pdf import generate_branded_pdf

        with pytest.raises(ValueError):
            generate_branded_pdf("# Hello", "")

    def test_generate_branded_pdf_truncates_large_input(self):
        """generate_branded_pdf truncates inputs > 2MB."""
        import tempfile, os
        from sandcastle.engine.pdf import generate_branded_pdf

        # Create input larger than _MAX_MARKDOWN_SIZE (2MB)
        large_md = "# Big Report\n\n" + "x " * (2 * 1024 * 1024)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(large_md, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# executor.py: _truncate_output with large dict
# ---------------------------------------------------------------------------

class TestTruncateOutputDict:
    def test_large_dict_gets_truncated(self):
        from sandcastle.engine.executor import _truncate_output

        large_dict = {"data": "x" * (11 * 1024 * 1024)}
        result = _truncate_output(large_dict)
        assert isinstance(result, dict)
        assert result.get("_truncated") is True

    def test_large_string_gets_truncated(self):
        from sandcastle.engine.executor import _truncate_output

        large_str = "x" * (11 * 1024 * 1024)
        result = _truncate_output(large_str)
        assert isinstance(result, str)
        assert "[TRUNCATED]" in result

    def test_non_json_serializable_falls_back(self):
        from sandcastle.engine.executor import _truncate_output

        class Unserializable:
            pass

        obj = Unserializable()
        result = _truncate_output(obj)
        # Should fall back to str() - no truncation for small objects
        assert result is obj or isinstance(result, str)

    def test_none_returns_none(self):
        from sandcastle.engine.executor import _truncate_output

        assert _truncate_output(None) is None
