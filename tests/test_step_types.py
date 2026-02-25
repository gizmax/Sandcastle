"""Tests for hybrid step types: llm, http, code, condition, classify, loop, transform, notify, delegate."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ClassifyConfig,
    CodeConfig,
    ConditionConfig,
    DelegateConfig,
    HttpConfig,
    LlmConfig,
    LoopConfig,
    NON_LLM_TYPES,
    NON_PROMPT_TYPES,
    NotifyConfig,
    StepDefinition,
    TransformConfig,
    VALID_STEP_TYPES,
    WorkflowDefinition,
    parse_yaml_string,
    validate,
)
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    resolve_templates,
    resolve_variable,
)

# Import private executor functions
_execute_code_step = _executor_mod._execute_code_step
_execute_condition_step = _executor_mod._execute_condition_step
_execute_http_step = _executor_mod._execute_http_step
_execute_llm_step = _executor_mod._execute_llm_step
_execute_loop_step = _executor_mod._execute_loop_step
_execute_classify_step = _executor_mod._execute_classify_step
_execute_transform_step = _executor_mod._execute_transform_step
_execute_notify_step = _executor_mod._execute_notify_step


# ---- DAG parsing tests ----

class TestStepTypeParsing:
    """Test YAML parsing for new step types."""

    def test_parse_llm_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: summarize
    type: llm
    prompt: "Summarize: {input.text}"
    model: haiku
    llm_config:
      system_prompt: "Be concise."
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("summarize")
        assert step.type == "llm"
        assert step.prompt == "Summarize: {input.text}"
        assert step.model == "haiku"
        assert step.llm_config is not None
        assert step.llm_config.system_prompt == "Be concise."

    def test_parse_http_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: fetch-data
    type: http
    http_config:
      url: "https://api.example.com/data/{input.id}"
      method: GET
      headers:
        Authorization: "Bearer {input.token}"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("fetch-data")
        assert step.type == "http"
        assert step.http_config is not None
        assert step.http_config.url == "https://api.example.com/data/{input.id}"
        assert step.http_config.method == "GET"
        assert "Authorization" in step.http_config.headers

    def test_parse_code_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: transform
    type: code
    code_config:
      code: |
        data = _steps["fetch"]
        result = [item["name"] for item in data]
      language: python
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("transform")
        assert step.type == "code"
        assert step.code_config is not None
        assert "result = [item" in step.code_config.code
        assert step.code_config.language == "python"

    def test_parse_condition_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: check-score
    type: condition
    condition_config:
      expression: "steps['score']['value'] > 80"
      then: [priority-path]
      else: [normal-path]
  - id: priority-path
    prompt: "Handle priority"
  - id: normal-path
    prompt: "Handle normal"
  - id: score
    prompt: "Score input"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("check-score")
        assert step.type == "condition"
        assert step.condition_config is not None
        assert step.condition_config.expression == "steps['score']['value'] > 80"
        assert step.condition_config.then_steps == ["priority-path"]
        assert step.condition_config.else_steps == ["normal-path"]

    def test_parse_classify_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: route-ticket
    type: classify
    classify_config:
      categories: [billing, technical, general]
      input: "{steps.parse.output.text}"
      model: haiku
      branches:
        billing: [billing-handler]
        technical: [tech-handler]
        general: [general-handler]
  - id: billing-handler
    prompt: "Handle billing"
  - id: tech-handler
    prompt: "Handle tech"
  - id: general-handler
    prompt: "Handle general"
  - id: parse
    prompt: "Parse input"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("route-ticket")
        assert step.type == "classify"
        assert step.classify_config is not None
        assert step.classify_config.categories == ["billing", "technical", "general"]
        assert step.classify_config.model == "haiku"
        assert "billing" in step.classify_config.branches

    def test_parse_loop_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: process-all
    type: loop
    depends_on: [fetch-items]
    loop_config:
      over: "{steps.fetch-items.output.items}"
      step_ids: [enrich, score]
      max_iterations: 50
  - id: fetch-items
    prompt: "Fetch items"
  - id: enrich
    prompt: "Enrich {input._item}"
  - id: score
    prompt: "Score {input._item}"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("process-all")
        assert step.type == "loop"
        assert step.loop_config is not None
        assert step.loop_config.step_ids == ["enrich", "score"]
        assert step.loop_config.max_iterations == 50

    def test_non_prompt_types_get_placeholder(self):
        """Non-prompt types should get a placeholder prompt."""
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: fetch
    type: http
    http_config:
      url: "https://example.com"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("fetch")
        assert step.prompt == "http step"  # placeholder

    def test_prompt_default_empty(self):
        """StepDefinition prompt should default to empty string."""
        step = StepDefinition(id="test")
        assert step.prompt == ""

    def test_valid_step_types_set(self):
        """All new types should be in VALID_STEP_TYPES."""
        for t in ["llm", "http", "code", "condition", "classify", "loop",
                   "transform", "notify", "delegate"]:
            assert t in VALID_STEP_TYPES

    def test_non_prompt_types(self):
        """http, code, condition, loop, transform, notify don't need prompts."""
        for t in ["http", "code", "condition", "loop", "transform", "notify"]:
            assert t in NON_PROMPT_TYPES

    def test_non_llm_types(self):
        """http, code, condition, loop, transform, notify don't use LLM models."""
        for t in ["http", "code", "condition", "loop", "transform", "notify"]:
            assert t in NON_LLM_TYPES


# ---- Validation tests ----

class TestStepTypeValidation:
    """Test validation rules for new step types."""

    def _make_wf(self, steps_yaml: str) -> WorkflowDefinition:
        yaml_content = f"""
name: test
description: Test workflow
default_model: sonnet
steps:
{steps_yaml}
"""
        return parse_yaml_string(yaml_content)

    def test_reject_unknown_type(self):
        wf = self._make_wf("""
  - id: bad
    type: unknown_type
    prompt: "test"
""")
        errors = validate(wf)
        assert any("unknown type" in e.lower() for e in errors)

    def test_http_requires_url(self):
        wf = self._make_wf("""
  - id: bad
    type: http
    http_config:
      method: GET
""")
        errors = validate(wf)
        assert any("url" in e.lower() for e in errors)

    def test_http_valid(self):
        wf = self._make_wf("""
  - id: good
    type: http
    http_config:
      url: "https://example.com"
""")
        errors = validate(wf)
        # No http-specific errors (there may be model errors for default_model)
        http_errors = [e for e in errors if "http" in e.lower()]
        assert len(http_errors) == 0

    def test_code_requires_code(self):
        wf = self._make_wf("""
  - id: bad
    type: code
    code_config:
      language: python
""")
        errors = validate(wf)
        assert any("code" in e.lower() and "must have" in e.lower() for e in errors)

    def test_condition_requires_expression(self):
        wf = self._make_wf("""
  - id: bad
    type: condition
    condition_config:
      then: [a]
""")
        errors = validate(wf)
        assert any("expression" in e.lower() for e in errors)

    def test_condition_validates_step_refs(self):
        wf = self._make_wf("""
  - id: check
    type: condition
    condition_config:
      expression: "True"
      then: [nonexistent]
""")
        errors = validate(wf)
        assert any("nonexistent" in e for e in errors)

    def test_classify_requires_categories(self):
        wf = self._make_wf("""
  - id: bad
    type: classify
    classify_config:
      input: "test"
""")
        errors = validate(wf)
        assert any("categories" in e.lower() for e in errors)

    def test_classify_validates_branch_refs(self):
        wf = self._make_wf("""
  - id: route
    type: classify
    classify_config:
      categories: [a, b]
      input: "test"
      branches:
        a: [nonexistent]
        b: [also-nonexistent]
""")
        errors = validate(wf)
        assert any("nonexistent" in e for e in errors)

    def test_loop_requires_over(self):
        wf = self._make_wf("""
  - id: bad
    type: loop
    loop_config:
      step_ids: [a]
""")
        errors = validate(wf)
        assert any("over" in e.lower() for e in errors)

    def test_non_llm_types_skip_model_validation(self):
        """http/code/condition/loop should not trigger model validation."""
        wf = self._make_wf("""
  - id: fetch
    type: http
    model: nonexistent_model
    http_config:
      url: "https://example.com"
""")
        errors = validate(wf)
        model_errors = [e for e in errors if "nonexistent_model" in e]
        assert len(model_errors) == 0

    def test_backward_compatibility(self):
        """Standard steps should still work without type field."""
        wf = self._make_wf("""
  - id: step1
    prompt: "Hello world"
""")
        step = wf.get_step("step1")
        assert step.type == "standard"


# ---- Executor tests ----

class TestCodeStepExecutor:
    """Test inline code execution."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={"text": "hello", "number": 42},
            step_outputs={"prev": {"items": [1, 2, 3]}},
        )

    @pytest.mark.asyncio
    async def test_simple_code(self, context):
        step = StepDefinition(
            id="transform",
            type="code",
            code_config=CodeConfig(code="result = len(_input['text'])"),
        )
        result = await _execute_code_step(step, context)
        assert result.status == "completed"
        assert result.output == 5
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_code_with_steps(self, context):
        step = StepDefinition(
            id="transform",
            type="code",
            code_config=CodeConfig(code='result = sum(_steps["prev"]["items"])'),
        )
        result = await _execute_code_step(step, context)
        assert result.status == "completed"
        assert result.output == 6

    @pytest.mark.asyncio
    async def test_code_json_module(self, context):
        step = StepDefinition(
            id="transform",
            type="code",
            code_config=CodeConfig(code='result = json.dumps({"key": "val"})'),
        )
        result = await _execute_code_step(step, context)
        assert result.status == "completed"
        assert "key" in result.output

    @pytest.mark.asyncio
    async def test_code_error_handling(self, context):
        step = StepDefinition(
            id="bad",
            type="code",
            code_config=CodeConfig(code="result = 1/0"),
        )
        result = await _execute_code_step(step, context)
        assert result.status == "failed"
        assert "division by zero" in result.error

    @pytest.mark.asyncio
    async def test_code_missing_config(self, context):
        step = StepDefinition(id="bad", type="code")
        result = await _execute_code_step(step, context)
        assert result.status == "failed"
        assert "Missing" in result.error


class TestConditionStepExecutor:
    """Test condition step branching."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={"threshold": 80},
            step_outputs={"score": {"value": 90}},
        )

    @pytest.mark.asyncio
    async def test_condition_true(self, context):
        step = StepDefinition(
            id="check",
            type="condition",
            condition_config=ConditionConfig(
                expression="steps['score']['value'] > 80",
                then_steps=["priority"],
                else_steps=["normal"],
            ),
        )
        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True
        # else_steps should be skipped
        assert "normal" in context.branch_skip_steps
        assert "priority" not in context.branch_skip_steps

    @pytest.mark.asyncio
    async def test_condition_false(self, context):
        context.step_outputs["score"] = {"value": 50}
        step = StepDefinition(
            id="check",
            type="condition",
            condition_config=ConditionConfig(
                expression="steps['score']['value'] > 80",
                then_steps=["priority"],
                else_steps=["normal"],
            ),
        )
        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is False
        # then_steps should be skipped
        assert "priority" in context.branch_skip_steps
        assert "normal" not in context.branch_skip_steps

    @pytest.mark.asyncio
    async def test_condition_with_input(self, context):
        step = StepDefinition(
            id="check",
            type="condition",
            condition_config=ConditionConfig(
                expression="input['threshold'] == 80",
                then_steps=["a"],
                else_steps=["b"],
            ),
        )
        result = await _execute_condition_step(step, context)
        assert result.status == "completed"
        assert result.output["condition"] is True

    @pytest.mark.asyncio
    async def test_condition_error(self, context):
        step = StepDefinition(
            id="check",
            type="condition",
            condition_config=ConditionConfig(
                expression="undefined_var > 0",
                then_steps=["a"],
                else_steps=["b"],
            ),
        )
        result = await _execute_condition_step(step, context)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_condition_zero_cost(self, context):
        step = StepDefinition(
            id="check",
            type="condition",
            condition_config=ConditionConfig(expression="True"),
        )
        result = await _execute_condition_step(step, context)
        assert result.cost_usd == 0.0


class TestHttpStepExecutor:
    """Test HTTP step execution (mocked)."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={"id": "123", "token": "abc"},
        )

    @pytest.mark.asyncio
    async def test_http_get(self, context):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            step = StepDefinition(
                id="fetch",
                type="http",
                http_config=HttpConfig(
                    url="https://api.example.com/data/{input.id}",
                    method="GET",
                ),
            )
            result = await _execute_http_step(step, context)

        assert result.status == "completed"
        assert result.output == {"data": "test"}
        assert result.cost_usd == 0.0
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert "123" in call_args.kwargs["url"]

    @pytest.mark.asyncio
    async def test_http_missing_config(self, context):
        step = StepDefinition(id="bad", type="http")
        result = await _execute_http_step(step, context)
        assert result.status == "failed"
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_http_with_auth(self, context):
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            step = StepDefinition(
                id="fetch",
                type="http",
                http_config=HttpConfig(
                    url="https://api.example.com",
                    method="GET",
                    auth="bearer:{input.token}",
                ),
            )
            result = await _execute_http_step(step, context)

        assert result.status == "completed"
        call_args = mock_client.request.call_args
        assert "Bearer abc" in call_args.kwargs["headers"]["Authorization"]


class TestLlmStepExecutor:
    """Test LLM step execution (mocked)."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={"text": "Hello world"},
        )

    @pytest.mark.asyncio
    async def test_llm_claude(self, context):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "Summary: Hello world is a greeting."}],
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("sandcastle.engine.providers.get_api_key", return_value="test-key"):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            step = StepDefinition(
                id="summarize",
                type="llm",
                prompt="Summarize: {input.text}",
                model="haiku",
                llm_config=LlmConfig(system_prompt="Be concise."),
            )
            result = await _execute_llm_step(step, context, AsyncMock())

        assert result.status == "completed"
        assert "Hello world" in result.output
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_llm_json_output(self, context):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": '{"summary": "test"}'}],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("sandcastle.engine.providers.get_api_key", return_value="test-key"):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            step = StepDefinition(
                id="summarize",
                type="llm",
                prompt="Summarize",
                model="haiku",
            )
            result = await _execute_llm_step(step, context, AsyncMock())

        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output["summary"] == "test"


class TestClassifyStepExecutor:
    """Test classify step execution (mocked)."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={},
            step_outputs={"parse": {"text": "I need to update my billing info"}},
        )

    @pytest.mark.asyncio
    async def test_classify_matches_category(self, context):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "billing"}],
            "usage": {"input_tokens": 50, "output_tokens": 5},
        }

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("sandcastle.engine.providers.get_api_key", return_value="test-key"):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            step = StepDefinition(
                id="route",
                type="classify",
                classify_config=ClassifyConfig(
                    categories=["billing", "technical", "general"],
                    input="{steps.parse.output.text}",
                    model="haiku",
                    branches={
                        "billing": ["billing-handler"],
                        "technical": ["tech-handler"],
                        "general": ["general-handler"],
                    },
                ),
            )
            result = await _execute_classify_step(step, context, AsyncMock())

        assert result.status == "completed"
        assert result.output["category"] == "billing"
        # Non-billing branches should be skipped
        assert "tech-handler" in context.branch_skip_steps
        assert "general-handler" in context.branch_skip_steps
        assert "billing-handler" not in context.branch_skip_steps


class TestLoopStepExecutor:
    """Test loop step execution."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={},
            step_outputs={"fetch": {"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}},
        )

    @pytest.mark.asyncio
    async def test_loop_basic(self, context):
        """Loop over items, executing sub-steps for each."""
        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[
                StepDefinition(id="fetch", prompt="Fetch"),
                StepDefinition(
                    id="process",
                    type="loop",
                    depends_on=["fetch"],
                    loop_config=LoopConfig(
                        over="{steps.fetch.output.items}",
                        step_ids=["transform"],
                        max_iterations=10,
                    ),
                ),
                StepDefinition(
                    id="transform",
                    type="code",
                    code_config=CodeConfig(code='result = _input["_item"]["name"].upper()'),
                ),
            ],
        )

        sandbox = AsyncMock()

        async def mock_execute(step, ctx, *a, **kw):
            item = ctx.input.get("_item", {})
            name = item.get("name", "") if isinstance(item, dict) else str(item)
            return StepResult(
                step_id=step.id,
                output=name.upper(),
                status="completed",
            )

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute,
        ):
            step = wf.get_step("process")
            result = await _execute_loop_step(step, context, sandbox, AsyncMock(), wf, 0)

        assert result.status == "completed"
        assert len(result.output) == 3

    @pytest.mark.asyncio
    async def test_loop_max_iterations(self, context):
        """Loop should respect max_iterations."""
        context.step_outputs["fetch"] = {"items": list(range(100))}

        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[
                StepDefinition(id="fetch", prompt="Fetch"),
                StepDefinition(
                    id="process",
                    type="loop",
                    loop_config=LoopConfig(over="{steps.fetch.output.items}", step_ids=["transform"], max_iterations=5),
                ),
                StepDefinition(
                    id="transform",
                    type="code",
                    code_config=CodeConfig(code="result = _input['_item'] * 2"),
                ),
            ],
        )

        async def mock_execute(step, ctx, *a, **kw):
            return StepResult(
                step_id=step.id,
                output=ctx.input["_item"] * 2,
                status="completed",
            )

        with patch(
            "sandcastle.engine.executor.execute_step_with_retry",
            side_effect=mock_execute,
        ):
            step = wf.get_step("process")
            result = await _execute_loop_step(step, context, AsyncMock(), AsyncMock(), wf, 0)

        assert result.status == "completed"
        assert len(result.output) == 5


class TestRunContextBranchSkip:
    """Test branch_skip_steps in RunContext."""

    def test_initial_empty(self):
        ctx = RunContext(run_id="test", input={})
        assert len(ctx.branch_skip_steps) == 0

    def test_with_item_copies_skip_steps(self):
        ctx = RunContext(run_id="test", input={})
        ctx.branch_skip_steps.add("skip-me")
        child = ctx.with_item({"data": 1}, 0)
        assert "skip-me" in child.branch_skip_steps
        # Modifying child should not affect parent
        child.branch_skip_steps.add("another")
        assert "another" not in ctx.branch_skip_steps


class TestResolveTemplatesWithNewTypes:
    """Test template resolution works with new step types."""

    def test_resolve_step_output(self):
        ctx = RunContext(
            run_id="test",
            input={"name": "World"},
            step_outputs={"fetch": {"url": "https://example.com"}},
        )
        result = resolve_templates("URL: {steps.fetch.output.url}", ctx)
        assert result == "URL: https://example.com"

    def test_resolve_input(self):
        ctx = RunContext(run_id="test", input={"name": "World"})
        result = resolve_templates("Hello {input.name}", ctx)
        assert result == "Hello World"


class TestBackwardCompatibility:
    """Ensure existing workflow YAML still works."""

    def test_standard_step_default(self):
        yaml_content = """
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "Hello world"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("step1")
        assert step.type == "standard"
        assert step.llm_config is None
        assert step.http_config is None
        assert step.code_config is None
        assert step.condition_config is None
        assert step.classify_config is None
        assert step.loop_config is None

    def test_approval_step_still_works(self):
        yaml_content = """
name: test
description: Test
default_model: sonnet
steps:
  - id: review
    type: approval
    approval_config:
      message: "Review needed"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("review")
        assert step.type == "approval"
        assert step.approval_config is not None

    def test_sub_workflow_still_works(self):
        yaml_content = """
name: test
description: Test
default_model: sonnet
steps:
  - id: sub
    type: sub_workflow
    sub_workflow:
      workflow: other-workflow
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("sub")
        assert step.type == "sub_workflow"
        assert step.sub_workflow is not None


class TestMixedWorkflow:
    """Test workflows that mix standard and hybrid step types."""

    def test_parse_mixed_workflow(self):
        yaml_content = """
name: mixed-demo
description: Mixed step types demo
default_model: sonnet

steps:
  - id: fetch
    type: http
    http_config:
      url: "https://api.example.com/data"

  - id: transform
    type: code
    depends_on: [fetch]
    code_config:
      code: |
        data = _steps["fetch"]
        result = [item for item in data.get("results", [])]

  - id: check
    type: condition
    depends_on: [transform]
    condition_config:
      expression: "len(steps['transform']) > 0"
      then: [analyze]
      else: [fallback]

  - id: analyze
    type: llm
    depends_on: [check]
    prompt: "Analyze this data: {steps.transform.output}"
    model: haiku

  - id: fallback
    depends_on: [check]
    prompt: "No data found, generate default report"
"""
        wf = parse_yaml_string(yaml_content)
        assert len(wf.steps) == 5
        assert wf.get_step("fetch").type == "http"
        assert wf.get_step("transform").type == "code"
        assert wf.get_step("check").type == "condition"
        assert wf.get_step("analyze").type == "llm"
        assert wf.get_step("fallback").type == "standard"

    def test_validate_mixed_workflow(self):
        yaml_content = """
name: mixed-demo
description: Mixed step types demo
default_model: sonnet

steps:
  - id: fetch
    type: http
    http_config:
      url: "https://api.example.com/data"

  - id: transform
    type: code
    depends_on: [fetch]
    code_config:
      code: "result = 42"

  - id: analyze
    type: llm
    depends_on: [transform]
    prompt: "Analyze"
    model: haiku
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        # Should have no errors
        assert len(errors) == 0, f"Unexpected errors: {errors}"


# ---- Phase 3: Transform step parsing tests ----

class TestTransformStepParsing:
    """Test YAML parsing for transform step type."""

    def test_parse_transform_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: format-output
    type: transform
    depends_on: [analyze]
    transform_config:
      template: '{"summary": "{steps.analyze.output.summary}", "score": {steps.analyze.output.score}}'
  - id: analyze
    prompt: "Analyze data"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("format-output")
        assert step.type == "transform"
        assert step.transform_config is not None
        assert "summary" in step.transform_config.template

    def test_parse_transform_multiline_template(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: render
    type: transform
    transform_config:
      template: |
        Report for {input.company}
        Score: {steps.score.output}
  - id: score
    prompt: "Score"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("render")
        assert step.type == "transform"
        assert "Report for" in step.transform_config.template

    def test_transform_placeholder_prompt(self):
        """Transform steps should get a placeholder prompt."""
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: fmt
    type: transform
    transform_config:
      template: "hello"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("fmt")
        assert step.prompt == "transform step"


# ---- Phase 3: Notify step parsing tests ----

class TestNotifyStepParsing:
    """Test YAML parsing for notify step type."""

    def test_parse_notify_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: alert-team
    type: notify
    notify_config:
      service: slack
      channel: "#alerts"
      message: "Workflow done: {steps.analyze.output.summary}"
  - id: analyze
    prompt: "Analyze"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("alert-team")
        assert step.type == "notify"
        assert step.notify_config is not None
        assert step.notify_config.service == "slack"
        assert step.notify_config.channel == "#alerts"
        assert "Workflow done" in step.notify_config.message

    def test_parse_notify_without_channel(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: webhook-notify
    type: notify
    notify_config:
      service: webhook
      message: "Build completed"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("webhook-notify")
        assert step.notify_config.service == "webhook"
        assert step.notify_config.channel == ""

    def test_notify_placeholder_prompt(self):
        """Notify steps should get a placeholder prompt."""
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: ping
    type: notify
    notify_config:
      service: slack
      message: "hello"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("ping")
        assert step.prompt == "notify step"


# ---- Phase 3: Delegate step parsing tests ----

class TestDelegateStepParsing:
    """Test YAML parsing for delegate step type."""

    def test_parse_delegate_step(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: enrich-data
    type: delegate
    delegate_config:
      workflow: data-enrichment
      task_description: "Enrich customer data from {input.source}"
      timeout: 1800
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("enrich-data")
        assert step.type == "delegate"
        assert step.delegate_config is not None
        assert step.delegate_config.workflow == "data-enrichment"
        assert "Enrich customer" in step.delegate_config.task_description
        assert step.delegate_config.timeout == 1800

    def test_parse_delegate_default_timeout(self):
        yaml_content = """
name: test
description: Test workflow
default_model: sonnet
steps:
  - id: sub-task
    type: delegate
    delegate_config:
      workflow: helper-workflow
      task_description: "Do something"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.get_step("sub-task")
        assert step.delegate_config.timeout == 3600

    def test_delegate_uses_llm_model(self):
        """Delegate steps should NOT be in NON_LLM_TYPES (they may run sub-workflows with LLMs)."""
        assert "delegate" not in NON_LLM_TYPES


# ---- Phase 3: Transform validation tests ----

class TestTransformStepValidation:
    """Test validation rules for transform step type."""

    def _make_wf(self, steps_yaml: str) -> WorkflowDefinition:
        yaml_content = f"""
name: test
description: Test workflow
default_model: sonnet
steps:
{steps_yaml}
"""
        return parse_yaml_string(yaml_content)

    def test_transform_requires_template(self):
        wf = self._make_wf("""
  - id: bad
    type: transform
    transform_config:
      template: ""
""")
        errors = validate(wf)
        assert any("template" in e.lower() for e in errors)

    def test_transform_valid(self):
        wf = self._make_wf("""
  - id: good
    type: transform
    transform_config:
      template: "Hello {input.name}"
""")
        errors = validate(wf)
        transform_errors = [e for e in errors if "transform" in e.lower()]
        assert len(transform_errors) == 0


# ---- Phase 3: Notify validation tests ----

class TestNotifyStepValidation:
    """Test validation rules for notify step type."""

    def _make_wf(self, steps_yaml: str) -> WorkflowDefinition:
        yaml_content = f"""
name: test
description: Test workflow
default_model: sonnet
steps:
{steps_yaml}
"""
        return parse_yaml_string(yaml_content)

    def test_notify_requires_service(self):
        wf = self._make_wf("""
  - id: bad
    type: notify
    notify_config:
      message: "hello"
""")
        errors = validate(wf)
        assert any("service" in e.lower() for e in errors)

    def test_notify_requires_message(self):
        wf = self._make_wf("""
  - id: bad
    type: notify
    notify_config:
      service: slack
""")
        errors = validate(wf)
        assert any("message" in e.lower() for e in errors)


# ---- Phase 3: Delegate validation tests ----

class TestDelegateStepValidation:
    """Test validation rules for delegate step type."""

    def _make_wf(self, steps_yaml: str) -> WorkflowDefinition:
        yaml_content = f"""
name: test
description: Test workflow
default_model: sonnet
steps:
{steps_yaml}
"""
        return parse_yaml_string(yaml_content)

    def test_delegate_requires_workflow(self):
        wf = self._make_wf("""
  - id: bad
    type: delegate
    delegate_config:
      task_description: "do something"
""")
        errors = validate(wf)
        assert any("workflow" in e.lower() for e in errors)

    def test_delegate_valid(self):
        wf = self._make_wf("""
  - id: good
    type: delegate
    delegate_config:
      workflow: helper-workflow
      task_description: "do something"
""")
        errors = validate(wf)
        delegate_errors = [e for e in errors if "delegate" in e.lower()]
        assert len(delegate_errors) == 0


# ---- Phase 3: Transform executor tests ----

class TestTransformStepExecutor:
    """Test transform step execution."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={"name": "World", "company": "Acme"},
            step_outputs={
                "analyze": {"summary": "All good", "score": 95},
                "fetch": {"items": [1, 2, 3]},
            },
        )

    @pytest.mark.asyncio
    async def test_simple_template(self, context):
        step = StepDefinition(
            id="format",
            type="transform",
            transform_config=TransformConfig(
                template="Hello {input.name}, score is {steps.analyze.output.score}",
            ),
        )
        result = await _execute_transform_step(step, context)
        assert result.status == "completed"
        assert "Hello World" in result.output
        assert "95" in result.output
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_jinja_tojson_filter(self, context):
        step = StepDefinition(
            id="format",
            type="transform",
            transform_config=TransformConfig(
                template='{{ steps.fetch.output.items | tojson }}',
            ),
        )
        result = await _execute_transform_step(step, context)
        assert result.status == "completed"
        assert result.output == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_transform_missing_config(self, context):
        step = StepDefinition(id="bad", type="transform")
        result = await _execute_transform_step(step, context)
        assert result.status == "failed"
        assert "Missing" in result.error


# ---- Phase 3: Notify executor tests ----

class TestNotifyStepExecutor:
    """Test notify step execution."""

    @pytest.fixture
    def context(self):
        return RunContext(
            run_id="test-run",
            input={"user": "admin"},
            step_outputs={"analyze": {"summary": "Build passed"}},
        )

    @pytest.mark.asyncio
    async def test_notify_success(self, context):
        step = StepDefinition(
            id="alert",
            type="notify",
            notify_config=NotifyConfig(
                service="slack",
                channel="#builds",
                message="Result: {steps.analyze.output.summary}",
            ),
        )
        result = await _execute_notify_step(step, context)
        assert result.status == "completed"
        assert result.output["service"] == "slack"
        assert result.output["channel"] == "#builds"
        assert "Build passed" in result.output["message"]
        assert result.output["status"] == "sent"
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_notify_missing_config(self, context):
        step = StepDefinition(id="bad", type="notify")
        result = await _execute_notify_step(step, context)
        assert result.status == "failed"
        assert "Missing" in result.error
