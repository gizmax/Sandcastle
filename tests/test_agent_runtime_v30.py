"""Tests for the universal type: agent step and runtime abstraction (v0.30)."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

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
from sandcastle.engine.agent_runtime import (
    AgentRuntime,
    AnthropicRuntime,
    AutoRuntime,
    LocalRuntime,
    RUNTIMES,
    get_runtime,
)
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    resolve_templates,
)

_execute_agent_step = _executor_mod._execute_agent_step
_execute_managed_agent_step = _executor_mod._execute_managed_agent_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_yaml(steps_yaml: str) -> str:
    """Wrap step YAML snippet in a minimal valid workflow."""
    return (
        "name: agent-runtime-test\n"
        "description: test universal agent step\n"
        "input_schema:\n"
        "  required: [topic]\n"
        "  properties:\n"
        "    topic:\n"
        "      type: string\n"
        "      description: topic\n"
        "steps:\n" + steps_yaml
    )


def _make_context(**kwargs) -> RunContext:
    """Create a minimal RunContext for testing."""
    return RunContext(
        run_id="test-run-001",
        input=kwargs.get("input", {"topic": "Python"}),
        workflow_name="agent-runtime-test",
    )


# ---------------------------------------------------------------------------
# 1. Runtime: AnthropicRuntime.is_available
# ---------------------------------------------------------------------------

class TestAnthropicRuntimeAvailability:
    """AnthropicRuntime.is_available depends on ANTHROPIC_API_KEY."""

    @pytest.mark.asyncio
    async def test_available_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-123")
        rt = AnthropicRuntime()
        assert await rt.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        rt = AnthropicRuntime()
        assert await rt.is_available() is False

    @pytest.mark.asyncio
    async def test_unavailable_with_empty_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        rt = AnthropicRuntime()
        assert await rt.is_available() is False

    def test_name(self):
        assert AnthropicRuntime.name == "anthropic"


# ---------------------------------------------------------------------------
# 2. Runtime: LocalRuntime.is_available
# ---------------------------------------------------------------------------

class TestLocalRuntimeAvailability:
    """LocalRuntime.is_available depends on Ollama being reachable."""

    @pytest.mark.asyncio
    async def test_available_with_ollama(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rt = LocalRuntime()
            assert await rt.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_ollama_down(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rt = LocalRuntime()
            assert await rt.is_available() is False

    def test_name(self):
        assert LocalRuntime.name == "local"


# ---------------------------------------------------------------------------
# 3. Runtime: AutoRuntime selection
# ---------------------------------------------------------------------------

class TestAutoRuntime:
    """AutoRuntime selects the best available backend."""

    @pytest.mark.asyncio
    async def test_selects_anthropic_first(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        rt = AutoRuntime()
        # Anthropic is first in the list, should be selected
        assert await rt.is_available() is True

    @pytest.mark.asyncio
    async def test_falls_back_to_local(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Mock Ollama as available
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rt = AutoRuntime()
            assert await rt.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_nothing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Mock Ollama as unavailable
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("down"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rt = AutoRuntime()
            assert await rt.is_available() is False

    @pytest.mark.asyncio
    async def test_execute_raises_when_nothing_available(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("down"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rt = AutoRuntime()
            with pytest.raises(RuntimeError, match="No agent runtime available"):
                await rt.execute(
                    system_prompt="test",
                    tools=[],
                    packages=[],
                    message="hello",
                    model="claude-sonnet-4-6",
                    timeout=60,
                    network="unrestricted",
                )

    def test_name(self):
        assert AutoRuntime.name == "auto"


# ---------------------------------------------------------------------------
# 4. get_runtime registry
# ---------------------------------------------------------------------------

class TestGetRuntime:
    """Test the get_runtime registry function."""

    def test_valid_names(self):
        for name in ("auto", "anthropic", "local"):
            rt = get_runtime(name)
            assert rt.name == name

    def test_invalid_name(self):
        with pytest.raises(ValueError, match="Unknown runtime 'quantum'"):
            get_runtime("quantum")

    def test_registry_contains_all(self):
        assert set(RUNTIMES.keys()) == {
            "auto",
            "anthropic",
            "local",
            "agent-sdk",
            "self-hosted-sandbox",
        }


# ---------------------------------------------------------------------------
# 5. DAG: type: agent in VALID_STEP_TYPES
# ---------------------------------------------------------------------------

class TestAgentStepType:
    """Verify 'agent' is a valid step type in the DAG parser."""

    def test_agent_in_valid_types(self):
        assert "agent" in VALID_STEP_TYPES
        assert "managed-agent" in VALID_STEP_TYPES

    def test_agent_in_non_prompt_types(self):
        assert "agent" in NON_PROMPT_TYPES

    def test_agent_in_non_llm_types(self):
        assert "agent" in NON_LLM_TYPES


# ---------------------------------------------------------------------------
# 6. DAG: type: agent YAML parsing
# ---------------------------------------------------------------------------

class TestAgentYamlParsing:
    """Test YAML parsing for the universal agent step."""

    def test_parse_agent_config(self):
        yaml_str = _base_yaml(
            '  - id: research\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: researcher\n'
            '      runtime: auto\n'
            '      message: "Research {input.topic}"\n'
            '      timeout: 300\n'
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.type == "agent"
        assert step.managed_agent_config is not None
        cfg = step.managed_agent_config
        assert cfg.agent_template == "researcher"
        assert cfg.runtime == "auto"
        assert cfg.message == "Research {input.topic}"
        assert cfg.timeout == 300

    def test_parse_managed_agent_config_still_works(self):
        yaml_str = _base_yaml(
            '  - id: research\n'
            '    type: managed-agent\n'
            '    managed_agent_config:\n'
            '      agent_template: researcher\n'
            '      message: "Research {input.topic}"\n'
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.type == "managed-agent"
        assert step.managed_agent_config is not None
        cfg = step.managed_agent_config
        assert cfg.agent_template == "researcher"

    def test_agent_config_and_managed_agent_config_both_work(self):
        """Both key names should populate managed_agent_config."""
        # agent_config
        yaml1 = _base_yaml(
            '  - id: a1\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: coder\n'
            '      message: "Write code"\n'
        )
        wf1 = parse_yaml_string(yaml1)
        assert wf1.steps[0].managed_agent_config.agent_template == "coder"

        # managed_agent_config
        yaml2 = _base_yaml(
            '  - id: a2\n'
            '    type: agent\n'
            '    managed_agent_config:\n'
            '      agent_template: coder\n'
            '      message: "Write code"\n'
        )
        wf2 = parse_yaml_string(yaml2)
        assert wf2.steps[0].managed_agent_config.agent_template == "coder"

    def test_template_alias_for_agent_template(self):
        """The 'template' key should work as alias for 'agent_template'."""
        yaml_str = _base_yaml(
            '  - id: a1\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: analyst\n'
            '      message: "Analyze data"\n'
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.agent_template == "analyst"

    def test_runtime_defaults_to_auto(self):
        yaml_str = _base_yaml(
            '  - id: a1\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: writer\n'
            '      message: "Write article"\n'
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.runtime == "auto"

    def test_runtime_explicit_local(self):
        yaml_str = _base_yaml(
            '  - id: a1\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: translator\n'
            '      runtime: local\n'
            '      message: "Translate text"\n'
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.runtime == "local"


# ---------------------------------------------------------------------------
# 7. DAG: validation for agent type
# ---------------------------------------------------------------------------

class TestAgentValidation:
    """Test that type: agent passes validation."""

    def test_agent_type_passes_validation(self):
        yaml_str = _base_yaml(
            '  - id: research\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: researcher\n'
            '      message: "Research topic"\n'
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        # Should have no errors about unknown type
        type_errors = [e for e in errors if "unknown type" in e.lower()]
        assert type_errors == []

    def test_managed_agent_type_still_valid(self):
        yaml_str = _base_yaml(
            '  - id: research\n'
            '    type: managed-agent\n'
            '    managed_agent_config:\n'
            '      agent_template: researcher\n'
            '      message: "Research topic"\n'
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        type_errors = [e for e in errors if "unknown type" in e.lower()]
        assert type_errors == []


# ---------------------------------------------------------------------------
# 8. ManagedAgentConfig: runtime field
# ---------------------------------------------------------------------------

class TestManagedAgentConfigRuntime:
    """ManagedAgentConfig has a runtime field defaulting to 'auto'."""

    def test_default_runtime(self):
        cfg = ManagedAgentConfig()
        assert cfg.runtime == "auto"

    def test_custom_runtime(self):
        cfg = ManagedAgentConfig(runtime="local")
        assert cfg.runtime == "local"

    def test_anthropic_runtime(self):
        cfg = ManagedAgentConfig(runtime="anthropic")
        assert cfg.runtime == "anthropic"


# ---------------------------------------------------------------------------
# 9. Executor: _execute_agent_step with mocked runtime
# ---------------------------------------------------------------------------

class TestExecuteAgentStep:
    """Test the _execute_agent_step executor function."""

    @pytest.mark.asyncio
    async def test_missing_config_returns_error(self):
        step = StepDefinition(id="test-step", type="agent")
        step.managed_agent_config = None
        ctx = _make_context()
        result = await _execute_agent_step(step, ctx, None)
        assert result.status == "failed"
        assert "agent_config" in result.error

    @pytest.mark.asyncio
    async def test_delegates_to_managed_agent_for_anthropic_runtime(self):
        """When runtime is 'anthropic', delegate to _execute_managed_agent_step."""
        step = StepDefinition(id="test-step", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="researcher",
            message="Research Python",
            runtime="anthropic",
        )
        ctx = _make_context()

        mock_result = StepResult(step_id="test-step", status="completed", output="Research done")
        with patch.object(_executor_mod, "_execute_managed_agent_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            result = await _execute_agent_step(step, ctx, None)
            assert result.status == "completed"
            assert result.output == "Research done"
            mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delegates_when_advanced_features_used(self):
        """When shared_files or fallback_template is set, use managed-agent path."""
        step = StepDefinition(id="test-step", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="coder",
            message="Write code",
            runtime="auto",
            shared_files=["prev-step"],
        )
        ctx = _make_context()

        mock_result = StepResult(step_id="test-step", status="completed", output="Code done")
        with patch.object(_executor_mod, "_execute_managed_agent_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            result = await _execute_agent_step(step, ctx, None)
            assert result.status == "completed"
            mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_with_mocked_local_runtime(self):
        """Test full execute flow using a mocked local runtime."""
        step = StepDefinition(id="test-step", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="writer",
            message="Write an article about {input.topic}",
            runtime="local",
        )
        ctx = _make_context()

        mock_runtime_obj = AsyncMock(spec=AgentRuntime)
        mock_runtime_obj.execute = AsyncMock(return_value={
            "output": "Article about Python",
            "tokens_in": 100,
            "tokens_out": 200,
            "runtime": "local",
        })

        # Patch at the source module since the executor imports inside the function
        with patch("sandcastle.engine.agent_runtime.get_runtime", return_value=mock_runtime_obj):
            result = await _execute_agent_step(step, ctx, None)
            assert result.status == "completed"
            assert result.output == "Article about Python"

    @pytest.mark.asyncio
    async def test_execute_json_output_format(self):
        """Test that output_format=json parses the agent output."""
        step = StepDefinition(id="json-step", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="analyst",
            message="Analyze data",
            runtime="local",
            output_format="json",
        )
        ctx = _make_context()

        mock_runtime_obj = AsyncMock(spec=AgentRuntime)
        mock_runtime_obj.execute = AsyncMock(return_value={
            "output": '{"key": "value", "count": 42}',
            "tokens_in": 50,
            "tokens_out": 100,
            "runtime": "local",
        })

        with patch("sandcastle.engine.agent_runtime.get_runtime", return_value=mock_runtime_obj):
            result = await _execute_agent_step(step, ctx, None)
            assert result.status == "completed"
            assert isinstance(result.output, dict)
            assert result.output["key"] == "value"
            assert result.output["count"] == 42

    @pytest.mark.asyncio
    async def test_execute_invalid_json_output(self):
        """Test that invalid JSON wraps in error envelope."""
        step = StepDefinition(id="bad-json", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="analyst",
            message="Analyze data",
            runtime="local",
            output_format="json",
        )
        ctx = _make_context()

        mock_runtime_obj = AsyncMock(spec=AgentRuntime)
        mock_runtime_obj.execute = AsyncMock(return_value={
            "output": "not valid json at all",
            "tokens_in": 10,
            "tokens_out": 20,
            "runtime": "local",
        })

        with patch("sandcastle.engine.agent_runtime.get_runtime", return_value=mock_runtime_obj):
            result = await _execute_agent_step(step, ctx, None)
            assert result.status == "completed"
            assert isinstance(result.output, dict)
            assert result.output["_parse_error"] is True
            assert "not valid json" in result.output["raw_text"]

    @pytest.mark.asyncio
    async def test_fallback_to_managed_agent_on_runtime_error(self):
        """When auto runtime fails, fall back to managed-agent path."""
        step = StepDefinition(id="fallback-step", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="coder",
            message="Write code",
            runtime="auto",
        )
        ctx = _make_context()

        mock_runtime_obj = AsyncMock(spec=AgentRuntime)
        mock_runtime_obj.execute = AsyncMock(side_effect=RuntimeError("Runtime crashed"))

        mock_managed_result = StepResult(
            step_id="fallback-step", status="completed", output="Fallback worked",
        )

        with patch("sandcastle.engine.agent_runtime.get_runtime", return_value=mock_runtime_obj):
            with patch.object(
                _executor_mod, "_execute_managed_agent_step", new_callable=AsyncMock,
            ) as mock_managed:
                mock_managed.return_value = mock_managed_result
                result = await _execute_agent_step(step, ctx, None)
                assert result.status == "completed"
                assert result.output == "Fallback worked"
                mock_managed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_auto_runtime_error_no_fallback(self):
        """When a specific runtime (not auto) fails, don't fall back."""
        step = StepDefinition(id="fail-step", type="agent")
        step.managed_agent_config = ManagedAgentConfig(
            agent_template="coder",
            message="Write code",
            runtime="local",
        )
        ctx = _make_context()

        mock_runtime_obj = AsyncMock(spec=AgentRuntime)
        mock_runtime_obj.execute = AsyncMock(side_effect=RuntimeError("Ollama down"))

        with patch("sandcastle.engine.agent_runtime.get_runtime", return_value=mock_runtime_obj):
            result = await _execute_agent_step(step, ctx, None)
            assert result.status == "failed"
            assert "Agent step failed" in result.error


# ---------------------------------------------------------------------------
# 10. Executor: hybrid dispatch includes agent type
# ---------------------------------------------------------------------------

class TestHybridDispatchAgent:
    """Verify the executor dispatches type: agent correctly."""

    def test_agent_in_hybrid_types(self):
        # The _HYBRID_TYPES is defined inside run_workflow, so we verify
        # indirectly by checking that agent is a valid type and handled
        assert "agent" in VALID_STEP_TYPES
        assert "agent" in NON_PROMPT_TYPES
        assert "agent" in NON_LLM_TYPES


# ---------------------------------------------------------------------------
# 11. Build plan with agent steps
# ---------------------------------------------------------------------------

class TestBuildPlanAgent:
    """Test that build_plan works correctly with agent steps."""

    def test_build_plan_with_agent_step(self):
        yaml_str = _base_yaml(
            '  - id: research\n'
            '    type: agent\n'
            '    agent_config:\n'
            '      template: researcher\n'
            '      message: "Research {input.topic}"\n'
            '  - id: write\n'
            '    type: agent\n'
            '    depends_on: [research]\n'
            '    agent_config:\n'
            '      template: writer\n'
            '      message: "Write about {steps.research.output}"\n'
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        # stages are list[list[str]] - lists of step ID strings
        assert len(plan.stages) >= 1
        step_ids = [sid for stage in plan.stages for sid in stage]
        assert "research" in step_ids
        assert "write" in step_ids


# ---------------------------------------------------------------------------
# 12. Runtime: AnthropicRuntime.name class attribute
# ---------------------------------------------------------------------------

class TestRuntimeNames:
    """Verify runtime name attributes."""

    def test_all_runtime_names(self):
        assert AnthropicRuntime.name == "anthropic"
        assert LocalRuntime.name == "local"
        assert AutoRuntime.name == "auto"

    def test_registry_names_match(self):
        for name, rt in RUNTIMES.items():
            assert rt.name == name
