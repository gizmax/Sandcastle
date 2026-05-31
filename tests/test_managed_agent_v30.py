"""Tests for the managed-agent step type: config, YAML parsing, validation, executor."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.engine.dag import (
    ManagedAgentConfig,
    NON_LLM_TYPES,
    NON_PROMPT_TYPES,
    StepDefinition,
    VALID_STEP_TYPES,
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

_execute_managed_agent_step = _executor_mod._execute_managed_agent_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_yaml(steps_yaml: str) -> str:
    """Wrap step YAML snippet in a minimal valid workflow."""
    return (
        "name: managed-agent-test\n"
        "description: test managed-agent step\n"
        "input_schema:\n"
        "  required: [topic]\n"
        "  properties:\n"
        "    topic:\n"
        "      type: string\n"
        "      description: topic\n"
        "steps:\n" + steps_yaml
    )


def _make_context(**overrides) -> RunContext:
    """Build a RunContext with sensible defaults."""
    defaults = dict(
        run_id="run-ma-1",
        input={"topic": "quantum computing"},
        step_outputs={},
        step_results={},
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _clear_managed_agent_caches():
    """Clear module-level agent/env caches between tests."""
    _executor_mod._managed_agent_cache.clear()
    _executor_mod._managed_env_cache.clear()


# ===================================================================
# 1. ManagedAgentConfig DEFAULTS
# ===================================================================

class TestManagedAgentConfigDefaults:
    """Verify default values of ManagedAgentConfig fields."""

    def test_defaults(self):
        """All fields should have sensible defaults."""
        cfg = ManagedAgentConfig()
        assert cfg.agent_id == ""
        assert cfg.environment_id == ""
        assert cfg.message == ""
        assert cfg.timeout == 600
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.tools_enabled is None
        assert cfg.stream is True
        assert cfg.system_prompt == ""
        assert cfg.packages is None
        assert cfg.network_access is True

    def test_custom_values(self):
        """Custom constructor values are preserved."""
        cfg = ManagedAgentConfig(
            agent_id="ag_123",
            environment_id="env_456",
            message="Hello {input.topic}",
            timeout=300,
            model="claude-opus-4",
            tools_enabled=["web_search", "bash"],
            stream=False,
            system_prompt="You are a research assistant.",
            packages=["pandas", "numpy"],
            network_access=False,
        )
        assert cfg.agent_id == "ag_123"
        assert cfg.environment_id == "env_456"
        assert cfg.message == "Hello {input.topic}"
        assert cfg.timeout == 300
        assert cfg.model == "claude-opus-4"
        assert cfg.tools_enabled == ["web_search", "bash"]
        assert cfg.stream is False
        assert cfg.system_prompt == "You are a research assistant."
        assert cfg.packages == ["pandas", "numpy"]
        assert cfg.network_access is False


# ===================================================================
# 2. YAML PARSING
# ===================================================================

class TestYamlParsing:
    """YAML parsing of managed_agent_config."""

    def test_minimal_config(self):
        """Minimal YAML with only agent_id."""
        yaml_str = _base_yaml(
            "  - id: delegate\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: auto\n"
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.type == "managed-agent"
        assert step.managed_agent_config is not None
        assert step.managed_agent_config.agent_id == "auto"
        assert step.managed_agent_config.timeout == 600
        assert step.managed_agent_config.model == "claude-sonnet-4-6"

    def test_full_config(self):
        """All managed_agent_config fields parsed correctly."""
        yaml_str = _base_yaml(
            "  - id: deep-research\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: ag_abc123\n"
            "      environment_id: env_xyz789\n"
            "      message: Research {input.topic}\n"
            "      timeout: 120\n"
            "      model: claude-opus-4\n"
            "      tools_enabled:\n"
            "        - web_search\n"
            "        - bash\n"
            "      stream: false\n"
            "      system_prompt: You are a research expert\n"
            "      packages:\n"
            "        - pandas\n"
            "        - numpy\n"
            "      network_access: false\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.agent_id == "ag_abc123"
        assert cfg.environment_id == "env_xyz789"
        assert cfg.message == "Research {input.topic}"
        assert cfg.timeout == 120
        assert cfg.model == "claude-opus-4"
        assert cfg.tools_enabled == ["web_search", "bash"]
        assert cfg.stream is False
        assert cfg.system_prompt == "You are a research expert"
        assert cfg.packages == ["pandas", "numpy"]
        assert cfg.network_access is False

    def test_no_config_key(self):
        """Type managed-agent without managed_agent_config => config is None."""
        yaml_str = _base_yaml(
            "  - id: bare\n"
            "    type: managed-agent\n"
        )
        wf = parse_yaml_string(yaml_str)
        assert wf.steps[0].managed_agent_config is None

    def test_empty_config(self):
        """Empty managed_agent_config: {} parses to defaults."""
        yaml_str = _base_yaml(
            "  - id: empty\n"
            "    type: managed-agent\n"
            "    managed_agent_config: {}\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg is not None
        assert cfg.agent_id == ""
        assert cfg.model == "claude-sonnet-4-6"

    def test_non_prompt_type_gets_placeholder(self):
        """managed-agent steps without prompt get a placeholder."""
        yaml_str = _base_yaml(
            "  - id: ma\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: auto\n"
        )
        wf = parse_yaml_string(yaml_str)
        assert wf.steps[0].prompt != ""

    def test_single_package_string_becomes_list(self):
        """A single string for packages is coerced to a list."""
        yaml_str = _base_yaml(
            "  - id: pkg\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: auto\n"
            "      packages: pandas\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.packages == ["pandas"]


# ===================================================================
# 3. TYPE SET MEMBERSHIP
# ===================================================================

class TestTypeSetMembership:
    """managed-agent must appear in the correct type sets."""

    def test_in_valid_step_types(self):
        """managed-agent is in VALID_STEP_TYPES."""
        assert "managed-agent" in VALID_STEP_TYPES

    def test_in_non_prompt_types(self):
        """managed-agent is in NON_PROMPT_TYPES (message comes from config)."""
        assert "managed-agent" in NON_PROMPT_TYPES

    def test_in_non_llm_types(self):
        """managed-agent is in NON_LLM_TYPES (uses its own API, not built-in LLM)."""
        assert "managed-agent" in NON_LLM_TYPES

    def test_step_type_count(self):
        """VALID_STEP_TYPES should have 25 entries (24 + tool)."""
        assert len(VALID_STEP_TYPES) == 25


# ===================================================================
# 4. VALIDATION
# ===================================================================

class TestValidation:
    """validate() checks for managed-agent steps."""

    def test_missing_config_fails(self):
        """Missing managed_agent_config -> validation error."""
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: managed-agent\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("managed_agent_config" in e.lower() or "agent_id" in e.lower() for e in errors), (
            f"Expected managed-agent validation error, got: {errors}"
        )

    def test_empty_agent_id_fails(self):
        """Empty agent_id -> validation error."""
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      message: do something\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("agent_id" in e.lower() for e in errors)

    def test_auto_agent_id_passes(self):
        """agent_id: auto should pass validation."""
        yaml_str = _base_yaml(
            "  - id: ok\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: auto\n"
            "      message: Research {input.topic}\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        managed_errors = [e for e in errors if "managed" in e.lower() or "agent_id" in e.lower()]
        assert len(managed_errors) == 0, f"Unexpected errors: {managed_errors}"

    def test_explicit_agent_id_passes(self):
        """Explicit agent_id should pass validation."""
        yaml_str = _base_yaml(
            "  - id: ok\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: ag_real_id_123\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        managed_errors = [e for e in errors if "managed" in e.lower() or "agent_id" in e.lower()]
        assert len(managed_errors) == 0

    def test_build_plan_works(self):
        """build_plan succeeds with a managed-agent step."""
        yaml_str = _base_yaml(
            "  - id: ma-step\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: auto\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        assert len(plan.stages) >= 1
        assert "ma-step" in plan.stages[0]


# ===================================================================
# 5. EXECUTOR
# ===================================================================

def _mock_sse_stream(events: list[dict]):
    """Create a mock httpx streaming response that yields SSE lines."""
    lines = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
    lines.append("")  # blank line at end

    class FakeStream:
        async def aiter_lines(self):
            for line in lines:
                yield line

    stream_ctx = AsyncMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=FakeStream())
    stream_ctx.__aexit__ = AsyncMock(return_value=False)
    return stream_ctx


class TestExecutor:
    """Tests for _execute_managed_agent_step with mocked HTTP."""

    def setup_method(self):
        """Clear caches before each test."""
        _clear_managed_agent_caches()

    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        """Without ANTHROPIC_API_KEY, step returns error."""
        step = StepDefinition(
            id="ma-1",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(agent_id="auto"),
        )
        ctx = _make_context()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            # Also ensure the key is absent (not set from other tests)
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                result = await _execute_managed_agent_step(step, ctx)
        assert result.status == "failed"
        assert "ANTHROPIC_API_KEY" in result.error

    @pytest.mark.asyncio
    async def test_missing_agent_id(self):
        """Without agent_id, step returns error immediately."""
        step = StepDefinition(
            id="ma-2",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(agent_id=""),
        )
        ctx = _make_context()
        result = await _execute_managed_agent_step(step, ctx)
        assert result.status == "failed"
        assert "agent_id" in result.error

    @pytest.mark.asyncio
    async def test_no_config(self):
        """Step without managed_agent_config fails."""
        step = StepDefinition(id="ma-3", type="managed-agent")
        ctx = _make_context()
        result = await _execute_managed_agent_step(step, ctx)
        assert result.status == "failed"
        assert "managed_agent_config" in result.error

    @pytest.mark.asyncio
    async def test_successful_auto_agent(self):
        """Full auto agent flow: create agent -> env -> session -> stream -> cleanup."""
        step = StepDefinition(
            id="ma-ok",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="Research {input.topic}",
                timeout=60,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()
        delete_urls = []

        # Mock POST responses
        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/agents" in url:
                resp.json.return_value = {"id": "ag_created_123"}
            elif "/environments" in url:
                resp.json.return_value = {"id": "env_created_456"}
            elif "/sessions" in url and "/events" not in url:
                resp.json.return_value = {"id": "sess_789"}
            else:
                resp.json.return_value = {}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)

        # Mock DELETE for session cleanup
        async def mock_delete(url, **kwargs):
            delete_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client.delete = AsyncMock(side_effect=mock_delete)

        # Mock SSE stream with usage data
        sse_events = [
            {
                "type": "agent.message",
                "content": [{"type": "text", "text": "Research findings: "}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
            {
                "type": "agent.message",
                "content": [{"type": "text", "text": "Quantum computing is..."}],
                "usage": {"input_tokens": 0, "output_tokens": 30},
            },
            {"type": "session.status_idle"},
        ]
        mock_client.stream = MagicMock(return_value=_mock_sse_stream(sse_events))

        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # Also mock the cleanup client
        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock(side_effect=mock_delete)
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        assert result.output == "Research findings: Quantum computing is..."
        assert result.input_prompt == "Research quantum computing"
        assert result.duration_seconds >= 0
        # Verify cost tracking from usage tokens (100 in, 80 out)
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_content_blocks_format(self):
        """User message must use content blocks format, not bare string."""
        step = StepDefinition(
            id="ma-content",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_existing",
                environment_id="env_existing",
                message="Hello world",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()
        captured_events_payload = {}

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/sessions" in url and "/events" in url:
                captured_events_payload.update(kwargs.get("json", {}))
                resp.json.return_value = {}
            elif "/sessions" in url:
                resp.json.return_value = {"id": "sess_1"}
            else:
                resp.json.return_value = {"id": "x"}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.stream = MagicMock(return_value=_mock_sse_stream([
            {"type": "agent.message", "content": [{"type": "text", "text": "OK"}]},
            {"type": "session.status_idle"},
        ]))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        # Verify content is list of content blocks, not bare string
        events = captured_events_payload.get("events", [])
        assert len(events) == 1
        content = events[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_existing_agent_id(self):
        """Existing agent_id skips agent creation."""
        step = StepDefinition(
            id="ma-existing",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_preexisting_999",
                message="Analyze data",
                timeout=60,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()
        post_urls = []

        async def mock_post(url, **kwargs):
            post_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            if "/environments" in url:
                resp.json.return_value = {"id": "env_1"}
            elif "/sessions" in url and "/events" not in url:
                resp.json.return_value = {"id": "sess_1"}
            else:
                resp.json.return_value = {}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.stream = MagicMock(return_value=_mock_sse_stream([
            {"type": "agent.message", "content": [{"type": "text", "text": "Done"}]},
            {"type": "session.status_idle"},
        ]))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        assert result.output == "Done"
        # Should NOT have called /agents endpoint
        agent_calls = [u for u in post_urls if u.endswith("/agents")]
        assert len(agent_calls) == 0

    @pytest.mark.asyncio
    async def test_environment_required_for_session(self):
        """When no environment_id provided, auto-create environment before session."""
        step = StepDefinition(
            id="ma-env",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_123",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()
        post_urls = []

        async def mock_post(url, **kwargs):
            post_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            if "/environments" in url:
                resp.json.return_value = {"id": "env_auto_created"}
            elif "/sessions" in url and "/events" not in url:
                resp.json.return_value = {"id": "sess_1"}
            else:
                resp.json.return_value = {}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.stream = MagicMock(return_value=_mock_sse_stream([
            {"type": "agent.message", "content": [{"type": "text", "text": "OK"}]},
            {"type": "session.status_idle"},
        ]))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        # Environment must be created before session
        env_calls = [u for u in post_urls if "/environments" in u]
        assert len(env_calls) == 1

    @pytest.mark.asyncio
    async def test_environment_creation_failure(self):
        """Environment creation failure returns error (environment is required)."""
        step = StepDefinition(
            id="ma-env-fail",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_123",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            if "/environments" in url:
                resp.status_code = 500
                resp.text = "Internal Server Error"
            else:
                resp.status_code = 200
                resp.json.return_value = {"id": "x"}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "failed"
        assert "environment" in result.error.lower() or "server error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_session_creation_failure(self):
        """Session creation failure returns error."""
        step = StepDefinition(
            id="ma-fail",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_123",
                environment_id="env_pre",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            if "/sessions" in url and "/events" not in url:
                resp.status_code = 500
                resp.text = "Internal Server Error"
            else:
                resp.status_code = 200
                resp.json.return_value = {"id": "env_1"}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "failed"
        assert "session" in result.error.lower() or "server error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_session_terminated_event(self):
        """session.terminated event also breaks the stream loop."""
        step = StepDefinition(
            id="ma-terminated",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_1",
                environment_id="env_1",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/sessions" in url and "/events" not in url:
                resp.json.return_value = {"id": "sess_1"}
            else:
                resp.json.return_value = {}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.stream = MagicMock(return_value=_mock_sse_stream([
            {"type": "agent.message", "content": [{"type": "text", "text": "partial"}]},
            {"type": "session.terminated"},
        ]))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        assert result.output == "partial"

    @pytest.mark.asyncio
    async def test_usage_tracking(self):
        """Token usage from SSE events is tracked and cost computed."""
        step = StepDefinition(
            id="ma-usage",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_1",
                environment_id="env_1",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/sessions" in url and "/events" not in url:
                resp.json.return_value = {"id": "sess_1"}
            else:
                resp.json.return_value = {}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.stream = MagicMock(return_value=_mock_sse_stream([
            {
                "type": "agent.message",
                "content": [{"type": "text", "text": "result"}],
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            },
            {"type": "session.status_idle"},
        ]))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        # 1000 input * 3.0/1M + 500 output * 15.0/1M = 0.003 + 0.0075 = 0.0105
        assert abs(result.cost_usd - 0.0105) < 0.001

    @pytest.mark.asyncio
    async def test_error_401_invalid_key(self):
        """401 response gives 'Invalid API key' error message."""
        step = StepDefinition(
            id="ma-401",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            if "/agents" in url:
                resp.status_code = 401
                resp.text = '{"error": "unauthorized"}'
            else:
                resp.status_code = 200
                resp.json.return_value = {"id": "x"}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "bad-key"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "failed"
        assert "Invalid API key" in result.error

    @pytest.mark.asyncio
    async def test_error_429_rate_limited(self):
        """429 response gives 'Rate limited' error message."""
        step = StepDefinition(
            id="ma-429",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            if "/agents" in url:
                resp.status_code = 429
                resp.text = '{"retry_after": 30}'
            else:
                resp.status_code = 200
                resp.json.return_value = {"id": "x"}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "failed"
        assert "Rate limited" in result.error

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """httpx.TimeoutException gives descriptive timeout error."""
        step = StepDefinition(
            id="ma-timeout",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_1",
                environment_id="env_1",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "failed"
        assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_agent_cache_reuse(self):
        """Auto-created agents are cached and reused across calls."""
        config = ManagedAgentConfig(
            agent_id="auto",
            message="Hello",
            timeout=30,
        )
        step1 = StepDefinition(
            id="ma-c1",
            type="managed-agent",
            managed_agent_config=config,
        )
        step2 = StepDefinition(
            id="ma-c2",
            type="managed-agent",
            managed_agent_config=config,
        )
        ctx = _make_context()

        agent_create_count = 0

        def make_mock():
            nonlocal agent_create_count
            mock_client = AsyncMock()
            agent_calls = []

            async def mock_post(url, **kwargs):
                nonlocal agent_create_count
                resp = MagicMock()
                resp.status_code = 200
                if url.endswith("/agents"):
                    agent_create_count += 1
                    resp.json.return_value = {"id": "ag_cached_1"}
                elif "/environments" in url:
                    resp.json.return_value = {"id": "env_cached_1"}
                elif "/sessions" in url and "/events" not in url:
                    resp.json.return_value = {"id": "sess_1"}
                else:
                    resp.json.return_value = {}
                return resp

            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client.stream = MagicMock(return_value=_mock_sse_stream([
                {"type": "agent.message", "content": [{"type": "text", "text": "ok"}]},
                {"type": "session.status_idle"},
            ]))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            return mock_client

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[make_mock(), mock_cleanup, make_mock(), mock_cleanup]):
                r1 = await _execute_managed_agent_step(step1, ctx)
                r2 = await _execute_managed_agent_step(step2, ctx)

        assert r1.status == "completed"
        assert r2.status == "completed"
        # Agent should only be created once (cached for second call)
        assert agent_create_count == 1

    @pytest.mark.asyncio
    async def test_system_prompt_sent_in_agent_creation(self):
        """system_prompt is passed in agent creation payload."""
        step = StepDefinition(
            id="ma-sys",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                system_prompt="You are a data analyst.",
                timeout=30,
            ),
        )
        ctx = _make_context()

        mock_client = AsyncMock()
        captured_agent_payload = {}

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/agents"):
                captured_agent_payload.update(kwargs.get("json", {}))
                resp.json.return_value = {"id": "ag_1"}
            elif "/environments" in url:
                resp.json.return_value = {"id": "env_1"}
            elif "/sessions" in url and "/events" not in url:
                resp.json.return_value = {"id": "sess_1"}
            else:
                resp.json.return_value = {}
            return resp

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.stream = MagicMock(return_value=_mock_sse_stream([
            {"type": "agent.message", "content": [{"type": "text", "text": "ok"}]},
            {"type": "session.status_idle"},
        ]))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cleanup = AsyncMock()
        mock_cleanup.delete = AsyncMock()
        mock_cleanup.__aenter__ = AsyncMock(return_value=mock_cleanup)
        mock_cleanup.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient", side_effect=[mock_client, mock_cleanup]):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed"
        assert captured_agent_payload.get("system") == "You are a data analyst."


# ===================================================================
# 6. TEMPLATE VALIDATION
# ===================================================================

class TestTemplateValidation:
    """Validate the managed_agent_research.yaml template."""

    def test_template_parses(self):
        """Template YAML parses without error."""
        from pathlib import Path

        template_path = (
            Path(__file__).parent.parent
            / "src" / "sandcastle" / "templates" / "managed_agent_research.yaml"
        )
        wf = parse_yaml_string(template_path.read_text())
        assert wf.name == "managed-agent-research"
        assert len(wf.steps) == 3

    def test_template_step_types_valid(self):
        """All step types in the template are valid."""
        from pathlib import Path

        template_path = (
            Path(__file__).parent.parent
            / "src" / "sandcastle" / "templates" / "managed_agent_research.yaml"
        )
        wf = parse_yaml_string(template_path.read_text())
        for step in wf.steps:
            assert step.type in VALID_STEP_TYPES, f"Invalid type: {step.type}"

    def test_template_validates(self):
        """Template passes validate() with no critical errors."""
        from pathlib import Path

        template_path = (
            Path(__file__).parent.parent
            / "src" / "sandcastle" / "templates" / "managed_agent_research.yaml"
        )
        wf = parse_yaml_string(template_path.read_text())
        errors = validate(wf)
        # Filter out non-critical warnings (e.g. about models)
        critical = [e for e in errors if "managed" in e.lower() or "agent" in e.lower()]
        assert len(critical) == 0, f"Validation errors: {critical}"

    def test_template_build_plan(self):
        """Template builds a valid execution plan."""
        from pathlib import Path

        template_path = (
            Path(__file__).parent.parent
            / "src" / "sandcastle" / "templates" / "managed_agent_research.yaml"
        )
        wf = parse_yaml_string(template_path.read_text())
        plan = build_plan(wf)
        assert len(plan.stages) >= 2  # research first, then format+report
