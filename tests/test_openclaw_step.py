"""Tests for the openclaw step type: YAML parsing, validation, and executor."""

from __future__ import annotations

import asyncio
import dataclasses
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.engine.dag import (
    NON_LLM_TYPES,
    NON_PROMPT_TYPES,
    OpenClawConfig,
    StepDefinition,
    VALID_STEP_TYPES,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    resolve_templates,
)

_execute_openclaw_step = _executor_mod._execute_openclaw_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_yaml(steps_yaml: str) -> str:
    """Wrap step YAML snippet in a minimal valid workflow."""
    return (
        "name: openclaw-test\n"
        "description: test openclaw step\n"
        "input_schema:\n"
        "  required: [query]\n"
        "  properties:\n"
        "    query:\n"
        "      type: string\n"
        "      description: query\n"
        "steps:\n" + steps_yaml
    )


def _make_context(**overrides) -> RunContext:
    """Build a RunContext with sensible defaults."""
    defaults = dict(
        run_id="run-oc-1",
        input={"query": "test query"},
        step_outputs={},
        step_results={},
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _mock_successful_response(response_text: str = "Agent response", model: str = "default") -> MagicMock:
    """Create a mock successful OpenAI-compatible gateway response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": response_text,
                    "role": "assistant",
                    "tool_calls": [],
                },
                "finish_reason": "stop",
            }
        ],
        "model": model,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _patch_httpx(mock_response: MagicMock):
    """Context manager helper to patch httpx.AsyncClient with a mock response."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return patch("httpx.AsyncClient", return_value=mock_client), mock_client


# ===================================================================
# 1. YAML PARSING
# ===================================================================

class TestYamlParsing:
    """YAML parsing of openclaw_config in all valid combinations."""

    def test_gateway_url_only(self):
        """Test 1: Minimal YAML with only gateway_url."""
        yaml_str = _base_yaml(
            "  - id: agent-call\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      gateway_url: http://localhost:8080\n"
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.type == "openclaw"
        assert step.openclaw_config is not None
        assert step.openclaw_config.gateway_url == "http://localhost:8080"
        assert step.openclaw_config.message == ""
        assert step.openclaw_config.skills == []
        assert step.openclaw_config.api_token == ""
        assert step.openclaw_config.timeout_seconds == 300
        assert step.openclaw_config.cost_per_call == 0.0

    def test_full_config(self):
        """Test 2: All openclaw_config fields parsed correctly."""
        yaml_str = _base_yaml(
            "  - id: agent-call\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      gateway_url: https://gateway.example.com\n"
            "      message: Analyze {input.query}\n"
            "      skills:\n"
            "        - web_search\n"
            "        - calculator\n"
            "      api_token: mytoken123\n"
            "      timeout_seconds: 120\n"
            "      cost_per_call: 0.05\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].openclaw_config
        assert cfg.gateway_url == "https://gateway.example.com"
        assert cfg.message == "Analyze {input.query}"
        assert cfg.skills == ["web_search", "calculator"]
        assert cfg.api_token == "mytoken123"
        assert cfg.timeout_seconds == 120
        assert cfg.cost_per_call == 0.05

    def test_openclaw_type_in_valid_step_types(self):
        """Test 3: 'openclaw' in VALID_STEP_TYPES."""
        assert "openclaw" in VALID_STEP_TYPES

    def test_openclaw_in_non_prompt_types(self):
        """Test 4: 'openclaw' in NON_PROMPT_TYPES (no prompt required)."""
        assert "openclaw" in NON_PROMPT_TYPES

    def test_openclaw_in_non_llm_types(self):
        """Test 5: 'openclaw' in NON_LLM_TYPES (no built-in LLM used)."""
        assert "openclaw" in NON_LLM_TYPES

    def test_non_prompt_type_gets_placeholder(self):
        """Test 6: Openclaw steps without a prompt get a placeholder."""
        yaml_str = _base_yaml(
            "  - id: oc\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      gateway_url: http://localhost:8080\n"
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.prompt != ""

    def test_no_openclaw_config_key(self):
        """Test 7: Type openclaw without openclaw_config key => openclaw_config is None."""
        yaml_str = _base_yaml(
            "  - id: bare\n"
            "    type: openclaw\n"
        )
        wf = parse_yaml_string(yaml_str)
        assert wf.steps[0].openclaw_config is None

    def test_empty_openclaw_config(self):
        """Test 8: Empty openclaw_config: {} parses to OpenClawConfig with defaults."""
        yaml_str = _base_yaml(
            "  - id: empty\n"
            "    type: openclaw\n"
            "    openclaw_config: {}\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].openclaw_config
        assert cfg is not None
        assert cfg.gateway_url == ""
        assert cfg.skills == []

    def test_cost_per_call_float_coercion(self):
        """Test 9: cost_per_call stored as float even when given as int in YAML."""
        yaml_str = _base_yaml(
            "  - id: oc\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      gateway_url: http://localhost:8080\n"
            "      cost_per_call: 1\n"
        )
        wf = parse_yaml_string(yaml_str)
        assert isinstance(wf.steps[0].openclaw_config.cost_per_call, float)
        assert wf.steps[0].openclaw_config.cost_per_call == 1.0


# ===================================================================
# 2. VALIDATION
# ===================================================================

class TestValidation:
    """validate() checks for openclaw steps."""

    def test_openclaw_without_openclaw_config_fails(self):
        """Test 10: Missing openclaw_config -> validation error."""
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: openclaw\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("openclaw_config" in e.lower() or "gateway_url" in e.lower() for e in errors), (
            f"Expected openclaw validation error, got: {errors}"
        )

    def test_openclaw_missing_gateway_url_fails(self):
        """Test 11: openclaw_config without gateway_url -> validation error."""
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      message: hello\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("gateway_url" in e.lower() for e in errors), (
            f"Expected gateway_url validation error, got: {errors}"
        )

    def test_valid_openclaw_passes(self):
        """Test 12: Valid openclaw_config with gateway_url -> no errors."""
        yaml_str = _base_yaml(
            "  - id: oc\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      gateway_url: http://localhost:8080\n"
            "      message: Do something useful\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_openclaw_does_not_require_prompt(self):
        """Test 13: Openclaw steps should NOT produce a 'missing prompt' error."""
        yaml_str = _base_yaml(
            "  - id: oc\n"
            "    type: openclaw\n"
            "    openclaw_config:\n"
            "      gateway_url: http://localhost:8080\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        prompt_errors = [e for e in errors if "prompt" in e.lower()]
        assert prompt_errors == [], f"Openclaw should not require prompt, got: {prompt_errors}"


# ===================================================================
# 3. EXECUTOR
# ===================================================================

class TestExecutorMissingConfig:
    """Executor failure cases for missing or invalid configuration."""

    @pytest.mark.asyncio
    async def test_missing_config(self):
        """Test 14: Missing openclaw_config -> status failed."""
        step = StepDefinition(id="oc1", type="openclaw", openclaw_config=None)
        ctx = _make_context()
        result = await _execute_openclaw_step(step, ctx)
        assert result.status == "failed"
        assert "openclaw_config" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_url_scheme(self):
        """Test 15: gateway_url without http(s) -> failed with helpful error."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="ftp://bad-scheme.com"),
        )
        ctx = _make_context()
        result = await _execute_openclaw_step(step, ctx)
        assert result.status == "failed"
        assert "http" in result.error.lower()

    @pytest.mark.asyncio
    async def test_connect_error_message(self):
        """Test 16: ConnectError -> 'Is it running?' hint in error message."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:19999"),
        )
        ctx = _make_context()

        async def mock_post(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await _execute_openclaw_step(step, ctx)

        assert result.status == "failed"
        assert "Is it running?" in result.error

    @pytest.mark.asyncio
    async def test_gateway_401_fails(self):
        """Test 17: Gateway returns 401 -> failed with HTTP status in error."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()

        async def mock_post(*args, **kwargs):
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 401
            raise httpx.HTTPStatusError(
                "401 Unauthorized",
                request=httpx.Request("POST", "http://localhost:8080/v1/chat/completions"),
                response=mock_resp,
            )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await _execute_openclaw_step(step, ctx)

        assert result.status == "failed"
        assert "401" in result.error

    @pytest.mark.asyncio
    async def test_gateway_500_fails(self):
        """Test 18: Gateway returns 500 -> failed."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()

        async def mock_post(*args, **kwargs):
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 500
            raise httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=httpx.Request("POST", "http://localhost:8080/v1/chat/completions"),
                response=mock_resp,
            )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await _execute_openclaw_step(step, ctx)

        assert result.status == "failed"
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_generic_exception_fails(self):
        """Test 19: Unexpected exception -> failed with error message."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()

        async def mock_post(*args, **kwargs):
            raise RuntimeError("Something went wrong")

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await _execute_openclaw_step(step, ctx)

        assert result.status == "failed"
        assert "Something went wrong" in result.error


class TestExecutorSuccess:
    """Executor success cases."""

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """Test 4 (success): Successful gateway response -> completed with output."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                message="Analyze this topic",
            ),
        )
        ctx = _make_context()
        mock_resp = _mock_successful_response("Great analysis result")

        patch_ctx, mock_client = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(step, ctx)

        assert result.status == "completed"
        assert result.output["response"] == "Great analysis result"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_empty_choices_response(self):
        """Test 20: Gateway returns empty choices -> empty response string."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [], "model": "default", "usage": {}}
        mock_resp.raise_for_status = MagicMock()

        patch_ctx, _ = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(step, ctx)

        assert result.status == "completed"
        assert result.output["response"] == ""
        assert result.output["skills_used"] == []

    @pytest.mark.asyncio
    async def test_cost_per_call_tracked(self):
        """Test 16 (cost): cost_per_call from config is stored in result."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                cost_per_call=0.042,
            ),
        )
        ctx = _make_context()
        mock_resp = _mock_successful_response()

        patch_ctx, _ = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(step, ctx)

        assert result.cost_usd == pytest.approx(0.042)

    @pytest.mark.asyncio
    async def test_duration_tracked(self):
        """Test 17 (duration): duration_seconds is >= 0."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()
        mock_resp = _mock_successful_response()

        patch_ctx, _ = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(step, ctx)

        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_input_prompt_stored(self):
        """Test 18 (input_prompt): The resolved message is stored in input_prompt."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                message="Process query: {input.query}",
            ),
        )
        ctx = _make_context(input={"query": "hello world"})
        mock_resp = _mock_successful_response()

        patch_ctx, _ = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(step, ctx)

        assert result.input_prompt is not None
        assert "hello world" in result.input_prompt

    @pytest.mark.asyncio
    async def test_template_resolution_in_message(self):
        """Test 11: Template variables in message are resolved before sending."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                message="Summarize: {steps.writer.output}",
            ),
        )
        ctx = _make_context(step_outputs={"writer": "The article content here"})

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["payload"] = json
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await _execute_openclaw_step(step, ctx)

        assert "The article content here" in captured["payload"]["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_template_resolution_in_gateway_url(self):
        """Test 12: Template variables in gateway_url are resolved."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://{input.host}:8080",
                message="Hello",
            ),
        )
        ctx = _make_context(input={"host": "myserver", "query": "test"})

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["url"] = url
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await _execute_openclaw_step(step, ctx)

        assert "myserver" in captured["url"]

    @pytest.mark.asyncio
    async def test_skills_passed_in_request(self):
        """Test 13: Skills listed in config are passed as tools in the request."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                skills=["web_search", "calculator"],
            ),
        )
        ctx = _make_context()

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["payload"] = json
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            await _execute_openclaw_step(step, ctx)

        tools = captured["payload"]["tools"]
        tool_names = [t["function"]["name"] for t in tools]
        assert "web_search" in tool_names
        assert "calculator" in tool_names

    @pytest.mark.asyncio
    async def test_api_token_from_config(self):
        """Test 14: api_token in config is sent as Bearer token."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                api_token="config-token-xyz",
            ),
        )
        ctx = _make_context()

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["headers"] = headers
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("OPENCLAW_API_TOKEN", None)
                await _execute_openclaw_step(step, ctx)

        assert captured["headers"]["Authorization"] == "Bearer config-token-xyz"

    @pytest.mark.asyncio
    async def test_api_token_from_env(self):
        """Test 15: OPENCLAW_API_TOKEN env var used when no config token."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                api_token="",  # empty -> fallback to env
            ),
        )
        ctx = _make_context()

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["headers"] = headers
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch.dict("os.environ", {"OPENCLAW_API_TOKEN": "env-token-abc"}):
                await _execute_openclaw_step(step, ctx)

        assert captured["headers"]["Authorization"] == "Bearer env-token-abc"

    @pytest.mark.asyncio
    async def test_no_token_no_auth_header(self):
        """No token in config or env -> no Authorization header."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(
                gateway_url="http://localhost:8080",
                api_token="",
            ),
        )
        ctx = _make_context()

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["headers"] = headers
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("OPENCLAW_API_TOKEN", None)
                await _execute_openclaw_step(step, ctx)

        assert "Authorization" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_model_sent_in_payload(self):
        """Model from step.model is forwarded to gateway."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            model="gpt-4-mini",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()

        captured = {}
        mock_resp = _mock_successful_response()

        async def mock_post(url, json=None, headers=None):
            captured["payload"] = json
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            await _execute_openclaw_step(step, ctx)

        assert captured["payload"]["model"] == "gpt-4-mini"

    @pytest.mark.asyncio
    async def test_usage_in_output(self):
        """Usage statistics from gateway response are included in output."""
        step = StepDefinition(
            id="oc1",
            type="openclaw",
            openclaw_config=OpenClawConfig(gateway_url="http://localhost:8080"),
        )
        ctx = _make_context()
        mock_resp = _mock_successful_response()

        patch_ctx, _ = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(step, ctx)

        assert "usage" in result.output
        assert result.output["usage"]["total_tokens"] == 30


# ===================================================================
# 4. FULL YAML ROUNDTRIP
# ===================================================================

class TestFullRoundtrip:
    """Test 20: Full roundtrip - parse YAML -> validate -> execute (mocked)."""

    @pytest.mark.asyncio
    async def test_full_roundtrip(self):
        """Parse YAML, validate (no errors), then execute with mock gateway."""
        yaml_str = _base_yaml(
            "  - id: research\n"
            "    prompt: research {input.query}\n"
            "  - id: agent-summary\n"
            "    type: openclaw\n"
            "    depends_on: [research]\n"
            "    openclaw_config:\n"
            "      gateway_url: http://localhost:8080\n"
            "      message: 'Summarize: {steps.research.output}'\n"
            "      skills: [summarizer]\n"
            "      cost_per_call: 0.01\n"
        )
        # Parse
        wf = parse_yaml_string(yaml_str)
        assert len(wf.steps) == 2
        agent_step = wf.get_step("agent-summary")
        assert agent_step.openclaw_config is not None
        assert agent_step.openclaw_config.gateway_url == "http://localhost:8080"

        # Validate
        errors = validate(wf)
        assert errors == [], f"Unexpected validation errors: {errors}"

        # Build plan (research must come before agent-summary)
        plan = build_plan(wf)
        research_stage = next(i for i, s in enumerate(plan.stages) if "research" in s)
        agent_stage = next(i for i, s in enumerate(plan.stages) if "agent-summary" in s)
        assert research_stage < agent_stage

        # Execute with mock
        ctx = _make_context(
            input={"query": "AI trends"},
            step_outputs={"research": "Research result about AI trends"},
        )
        mock_resp = _mock_successful_response("Concise AI summary")
        patch_ctx, _ = _patch_httpx(mock_resp)
        with patch_ctx:
            result = await _execute_openclaw_step(agent_step, ctx)

        assert result.status == "completed"
        assert result.output["response"] == "Concise AI summary"
        assert result.cost_usd == pytest.approx(0.01)
        assert "Research result about AI trends" in result.input_prompt


# ===================================================================
# 5. OpenClawConfig DATACLASS
# ===================================================================

class TestOpenClawConfigDataclass:
    """Direct tests on the OpenClawConfig dataclass."""

    def test_defaults(self):
        cfg = OpenClawConfig()
        assert cfg.gateway_url == ""
        assert cfg.message == ""
        assert cfg.skills == []
        assert cfg.api_token == ""
        assert cfg.timeout_seconds == 300
        assert cfg.cost_per_call == 0.0

    def test_custom_values(self):
        cfg = OpenClawConfig(
            gateway_url="http://my-gateway:9090",
            message="Do work",
            skills=["tool_a", "tool_b"],
            api_token="tok-abc",
            timeout_seconds=60,
            cost_per_call=0.1,
        )
        assert cfg.gateway_url == "http://my-gateway:9090"
        assert cfg.message == "Do work"
        assert cfg.skills == ["tool_a", "tool_b"]
        assert cfg.api_token == "tok-abc"
        assert cfg.timeout_seconds == 60
        assert cfg.cost_per_call == 0.1

    def test_field_count(self):
        fields = dataclasses.fields(OpenClawConfig)
        assert len(fields) == 6, (
            f"Expected 6 fields in OpenClawConfig, got {len(fields)}: "
            f"{[f.name for f in fields]}"
        )


# ===================================================================
# 6. StepDefinition has openclaw_config field
# ===================================================================

class TestStepDefinitionOpenclaw:
    """StepDefinition should have openclaw_config field."""

    def test_openclaw_config_field_exists(self):
        field_names = [f.name for f in dataclasses.fields(StepDefinition)]
        assert "openclaw_config" in field_names

    def test_openclaw_config_defaults_to_none(self):
        step = StepDefinition(id="x")
        assert step.openclaw_config is None
