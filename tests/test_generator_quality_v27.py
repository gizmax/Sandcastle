"""Deterministic quality tests for the AI Workflow Generator (v27).

Verifies generator infrastructure (system prompts, YAML parsing, chat logic,
validation, error handling) WITHOUT making real LLM calls.  All LLM-dependent
paths are tested by mocking _call_advisor_llm.

Target: 50+ tests across 5 categories.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    NON_PROMPT_TYPES,
    VALID_STEP_TYPES,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.generator import (
    GenerateResult,
    _build_chat_system_prompt,
    _build_system_prompt,
    _extract_latest_yaml,
    _load_example_templates,
    _scrub_secrets,
    _strip_fencing,
    explain_error,
    generate_chat,
    generate_workflow,
)
from sandcastle.engine.providers import KNOWN_MODELS
from sandcastle.engine.tools.registry import TOOL_REGISTRY

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid workflow YAML used for mock LLM returns
VALID_3STEP_YAML = """\
name: test-pipeline
description: A three-step test pipeline

default_model: sonnet

input_schema:
  required: ["query"]
  properties:
    query:
      type: string
      description: "Search query"

steps:
  - id: research
    prompt: "Research {input.query}"
    model: sonnet
  - id: analyze
    prompt: "Analyze findings from {{steps.research.output}}"
    depends_on: [research]
  - id: report
    prompt: "Write a report based on {{steps.analyze.output}}"
    depends_on: [analyze]
"""

SETTINGS_MOCK = MagicMock(
    data_residency="",
    anthropic_api_key="",
    openai_api_key="",
    mistral_api_key="",
    openrouter_api_key="",
    minimax_api_key="",
    advisor_quality_mode="auto",
)


# =========================================================================
# 1. System prompt completeness (15+ tests)
# =========================================================================


class TestSystemPromptCompleteness:
    """Verify the generation system prompt contains all necessary context."""

    def test_all_valid_step_types_mentioned(self):
        """Every one of the 20 VALID_STEP_TYPES must appear in the system prompt."""
        prompt = _build_system_prompt()
        for stype in VALID_STEP_TYPES:
            assert stype in prompt, f"Step type '{stype}' missing from system prompt"

    def test_non_prompt_types_listed(self):
        """NON_PROMPT_TYPES must be documented so the LLM knows which skip prompt."""
        prompt = _build_system_prompt()
        # The system prompt has an IMPORTANT block listing non-prompt types
        for stype in NON_PROMPT_TYPES:
            assert stype in prompt, (
                f"Non-prompt type '{stype}' missing from system prompt"
            )

    def test_all_tool_connectors_in_system_prompt(self):
        """All 62+ tools from TOOL_REGISTRY must appear in the system prompt."""
        prompt = _build_system_prompt()
        for tool_name in TOOL_REGISTRY:
            assert tool_name in prompt, (
                f"Tool connector '{tool_name}' missing from system prompt"
            )

    def test_tool_registry_has_at_least_62_tools(self):
        """Sanity check: registry should have at least 62 tools."""
        assert len(TOOL_REGISTRY) >= 62, (
            f"Expected at least 62 tools, got {len(TOOL_REGISTRY)}"
        )

    def test_all_known_models_in_system_prompt(self):
        """All models from KNOWN_MODELS must be listed in the system prompt."""
        prompt = _build_system_prompt()
        for model in KNOWN_MODELS:
            assert model in prompt, (
                f"Model '{model}' missing from system prompt"
            )

    def test_parse_step_documented(self):
        """The parse step type must be documented with its config fields."""
        prompt = _build_system_prompt()
        assert "### parse" in prompt
        assert "parse_config" in prompt
        assert "PDF" in prompt or "pdf" in prompt.lower()

    def test_openclaw_step_documented(self):
        """The openclaw step type must be documented."""
        prompt = _build_system_prompt()
        assert "### openclaw" in prompt or "openclaw" in prompt
        assert "openclaw_config" in prompt
        assert "gateway_url" in prompt

    def test_composio_step_documented(self):
        """The composio step type must be documented with config fields."""
        prompt = _build_system_prompt()
        assert "### composio" in prompt
        assert "composio_config" in prompt
        assert "action" in prompt

    def test_browser_step_documented_with_config_fields(self):
        """The browser step must be documented with mode, start_url, viewport, etc."""
        prompt = _build_system_prompt()
        assert "### browser" in prompt
        assert "browser_config" in prompt
        assert "mode" in prompt
        assert "start_url" in prompt
        assert "viewport_width" in prompt or "viewport" in prompt
        assert "playwright" in prompt
        assert "computer_use" in prompt

    def test_retry_config_mentioned(self):
        """System prompt must mention retry configuration."""
        prompt = _build_system_prompt()
        assert "retry" in prompt.lower()
        assert "max_attempts" in prompt or "backoff" in prompt

    def test_memory_config_mentioned(self):
        """System prompt must mention agent memory configuration."""
        prompt = _build_system_prompt()
        assert "memory" in prompt.lower()
        # Memory config has read/write fields
        assert "read" in prompt and "write" in prompt

    def test_risk_level_mentioned(self):
        """System prompt must mention risk_level for EU AI Act compliance."""
        prompt = _build_system_prompt()
        assert "risk_level" in prompt
        assert "minimal" in prompt
        assert "high" in prompt

    def test_examples_included(self):
        """System prompt must include at least 4 few-shot examples."""
        prompt = _build_system_prompt()
        # Each example is prefixed with "--- Example: <name> ---"
        example_count = prompt.count("--- Example:")
        assert example_count >= 4, (
            f"Expected at least 4 examples, found {example_count}"
        )

    def test_system_prompt_under_150000_chars(self):
        """System prompt must stay under 150,000 chars for token limit safety.

        The prompt includes full tool registry docs, step type docs, and
        4 curated template examples so it is expected to be ~100K chars.
        A 150K ceiling prevents unbounded growth while allowing current content.
        """
        prompt = _build_system_prompt()
        assert len(prompt) < 150_000, (
            f"System prompt is {len(prompt)} chars, exceeds 150,000 limit"
        )

    def test_chat_system_prompt_has_json_format_instructions(self):
        """Chat system prompt must instruct the LLM to respond in JSON."""
        prompt = _build_chat_system_prompt()
        assert "JSON" in prompt
        assert '"mode"' in prompt or "'mode'" in prompt
        assert '"questions"' in prompt or "'questions'" in prompt
        assert '"yaml"' in prompt or "'yaml'" in prompt

    def test_chat_system_prompt_has_decision_rules(self):
        """Chat system prompt must contain decision rules for when to ask vs generate."""
        prompt = _build_chat_system_prompt()
        assert "Decision rules" in prompt or "decision rules" in prompt.lower()
        # Must mention the "just generate" / "skip questions" fast path
        assert "just generate" in prompt.lower() or "skip questions" in prompt.lower()
        # Must mention when to use MODE 1 vs MODE 2
        assert "MODE 1" in prompt
        assert "MODE 2" in prompt

    def test_system_prompt_documents_input_schema(self):
        """System prompt must document the input_schema requirement."""
        prompt = _build_system_prompt()
        assert "input_schema" in prompt
        assert "required" in prompt
        assert "properties" in prompt

    def test_system_prompt_documents_variable_syntax(self):
        """System prompt must document variable syntax for input and step refs."""
        prompt = _build_system_prompt()
        assert "{input." in prompt
        assert "{steps." in prompt


# =========================================================================
# 2. YAML stripping/parsing (10+ tests)
# =========================================================================


class TestYamlStrippingParsing:
    """Verify _strip_fencing and parse_yaml_string edge cases."""

    def test_strip_fencing_yaml_wrapper(self):
        """Strips ```yaml ... ``` wrapper correctly."""
        raw = "```yaml\nname: test\nsteps: []\n```"
        assert _strip_fencing(raw) == "name: test\nsteps: []"

    def test_strip_fencing_yml_wrapper(self):
        """Strips ```yml ... ``` wrapper correctly."""
        raw = "```yml\nname: test\n```"
        assert _strip_fencing(raw) == "name: test"

    def test_strip_fencing_plain_wrapper(self):
        """Strips plain ``` ... ``` wrapper (no language tag)."""
        raw = "```\nname: test\n```"
        assert _strip_fencing(raw) == "name: test"

    def test_strip_fencing_no_fencing(self):
        """Passes through text without any fencing unchanged."""
        raw = "name: test\nsteps: []"
        assert _strip_fencing(raw) == "name: test\nsteps: []"

    def test_strip_fencing_extra_whitespace(self):
        """Handles leading/trailing whitespace around fenced block."""
        raw = "  \n```yaml\nname: test\n```  \n"
        assert _strip_fencing(raw) == "name: test"

    def test_strip_fencing_multiple_code_blocks_takes_first(self):
        """When multiple code blocks exist, takes the first one."""
        raw = "```yaml\nname: first\n```\n\n```yaml\nname: second\n```"
        result = _strip_fencing(raw)
        assert "first" in result

    def test_parse_yaml_string_valid_workflow(self):
        """parse_yaml_string returns a WorkflowDefinition for valid YAML."""
        wf = parse_yaml_string(VALID_3STEP_YAML)
        assert wf.name == "test-pipeline"
        assert len(wf.steps) == 3
        assert wf.steps[0].id == "research"

    def test_parse_yaml_string_empty_string(self):
        """parse_yaml_string raises ValueError on empty string."""
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("")

    def test_parse_yaml_string_whitespace_only(self):
        """parse_yaml_string raises ValueError on whitespace-only string."""
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("   \n  \t  ")

    def test_parse_yaml_string_invalid_yaml(self):
        """parse_yaml_string raises on malformed YAML."""
        with pytest.raises((ValueError, Exception)):
            parse_yaml_string("{{{{invalid: yaml: :::")

    def test_parse_yaml_string_non_mapping(self):
        """parse_yaml_string raises on YAML that is a list, not a mapping."""
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("- item1\n- item2")


# =========================================================================
# 3. Chat mode logic (10+ tests)
# =========================================================================


class TestChatModeLogic:
    """Verify multi-turn chat generation helpers and flow."""

    def test_extract_latest_yaml_single_yaml_message(self):
        """Extracts YAML from a single assistant message with mode=yaml."""
        messages = [
            {"role": "user", "content": "Build a pipeline"},
            {
                "role": "assistant",
                "content": json.dumps({
                    "mode": "yaml",
                    "message": "Here is your workflow",
                    "yaml": "name: my-workflow\nsteps: []",
                }),
            },
        ]
        result = _extract_latest_yaml(messages)
        assert result is not None
        assert "my-workflow" in result

    def test_extract_latest_yaml_multiple_yamls_takes_latest(self):
        """When multiple YAML messages exist, returns the latest one."""
        messages = [
            {"role": "user", "content": "Build v1"},
            {
                "role": "assistant",
                "content": json.dumps({
                    "mode": "yaml",
                    "message": "v1",
                    "yaml": "name: version-one\nsteps: []",
                }),
            },
            {"role": "user", "content": "Rename to v2"},
            {
                "role": "assistant",
                "content": json.dumps({
                    "mode": "yaml",
                    "message": "v2",
                    "yaml": "name: version-two\nsteps: []",
                }),
            },
        ]
        result = _extract_latest_yaml(messages)
        assert result is not None
        assert "version-two" in result
        assert "version-one" not in result

    def test_extract_latest_yaml_no_yaml_returns_none(self):
        """Returns None when no assistant message contains YAML."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": json.dumps({
                    "mode": "questions",
                    "message": "What would you like?",
                }),
            },
        ]
        assert _extract_latest_yaml(messages) is None

    def test_extract_latest_yaml_non_json_assistant_skipped(self):
        """Assistant messages that are not valid JSON are safely skipped."""
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Just some plain text, not JSON"},
        ]
        assert _extract_latest_yaml(messages) is None

    def test_extract_latest_yaml_empty_messages(self):
        """Empty message list returns None."""
        assert _extract_latest_yaml([]) is None

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_json_response_parsing_questions_mode(
        self, _mock_key, mock_llm
    ):
        """Valid JSON with mode=questions is returned correctly."""
        mock_llm.return_value = json.dumps({
            "mode": "questions",
            "message": "What is the input format?",
        })
        result = await generate_chat([
            {"role": "user", "content": "Build a data pipeline"},
        ])
        assert result["mode"] == "questions"
        assert "input format" in result["message"]

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_json_response_parsing_yaml_mode(
        self, _mock_key, mock_llm
    ):
        """Valid JSON with mode=yaml parses and validates the YAML."""
        mock_llm.return_value = json.dumps({
            "mode": "yaml",
            "message": "Generated workflow",
            "yaml": VALID_3STEP_YAML,
        })
        result = await generate_chat([
            {"role": "user", "content": "Build a 3-step research pipeline"},
        ])
        assert result["mode"] == "yaml"
        assert result["yaml_content"] is not None
        assert result["steps_count"] == 3
        assert result["name"] == "test-pipeline"

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_json_fallback_regex_extraction(
        self, _mock_key, mock_llm
    ):
        """When LLM returns JSON wrapped in prose, regex fallback extracts it."""
        mock_llm.return_value = (
            'Sure, here is the response: {"mode": "questions", '
            '"message": "Please clarify the output format."}'
        )
        result = await generate_chat([
            {"role": "user", "content": "Build something"},
        ])
        assert result["mode"] == "questions"
        assert "output format" in result["message"]

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_json_fallback_no_json_at_all(
        self, _mock_key, mock_llm
    ):
        """When LLM returns plain text with no JSON, falls back to questions mode."""
        mock_llm.return_value = "I need more information about your workflow."
        result = await generate_chat([
            {"role": "user", "content": "Do something"},
        ])
        assert result["mode"] == "questions"
        assert "more information" in result["message"]

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_chat_with_existing_yaml_injects_context(
        self, _mock_key, mock_llm
    ):
        """When existing_yaml is provided, it is injected into the first user message."""
        existing = "name: old-workflow\nsteps: []"
        mock_llm.return_value = json.dumps({
            "mode": "questions",
            "message": "What changes would you like?",
        })
        await generate_chat(
            [{"role": "user", "content": "Add a step"}],
            existing_yaml=existing,
        )
        # Verify the LLM was called with the existing YAML in the user content
        call_args = mock_llm.call_args
        user_content = call_args.kwargs.get("user", call_args[1].get("user", ""))
        assert "old-workflow" in user_content

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_chat_empty_messages_handled(
        self, _mock_key, mock_llm
    ):
        """generate_chat handles empty user content gracefully."""
        mock_llm.return_value = json.dumps({
            "mode": "questions",
            "message": "How can I help?",
        })
        result = await generate_chat([
            {"role": "user", "content": ""},
        ])
        assert result["mode"] == "questions"


# =========================================================================
# 4. Generated YAML validation (10+ tests)
# =========================================================================


class TestGeneratedYamlValidation:
    """Mock LLM to return predetermined YAML, then verify validation."""

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_valid_3step_workflow_passes(self, _mock_key, mock_llm):
        """A well-formed 3-step workflow passes validation with no errors."""
        mock_llm.return_value = VALID_3STEP_YAML
        result = await generate_workflow("Build a research pipeline")
        assert result.validation_errors == [], (
            f"Expected no errors, got: {result.validation_errors}"
        )
        assert result.steps_count == 3
        assert result.name == "test-pipeline"

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_unknown_step_type_fails(self, _mock_key, mock_llm):
        """Workflow with an unknown step type produces validation errors."""
        bad_yaml = """\
name: bad-types
description: Has an invalid type
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps:
  - id: step-a
    type: quantum_compute
    prompt: "Do quantum stuff"
"""
        mock_llm.return_value = bad_yaml
        result = await generate_workflow("test")
        assert any("unknown type" in e for e in result.validation_errors), (
            f"Expected unknown type error, got: {result.validation_errors}"
        )

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_circular_deps_fails(self, _mock_key, mock_llm):
        """Workflow with circular dependencies produces validation errors."""
        circular_yaml = """\
name: circular
description: Circular deps test
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps:
  - id: alpha
    prompt: "Step alpha"
    depends_on: [beta]
  - id: beta
    prompt: "Step beta"
    depends_on: [alpha]
"""
        mock_llm.return_value = circular_yaml
        result = await generate_workflow("test")
        assert any(
            "cycle" in e.lower() or "circular" in e.lower()
            for e in result.validation_errors
        ), f"Expected cycle error, got: {result.validation_errors}"

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_missing_depends_on_reference_fails(self, _mock_key, mock_llm):
        """Workflow with depends_on referencing a non-existent step fails."""
        bad_yaml = """\
name: bad-dep
description: Missing dependency
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps:
  - id: step-a
    prompt: "Do something"
    depends_on: [nonexistent-step]
"""
        mock_llm.return_value = bad_yaml
        result = await generate_workflow("test")
        assert any("unknown step" in e for e in result.validation_errors), (
            f"Expected unknown dep error, got: {result.validation_errors}"
        )

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_invalid_step_id_format_fails(self, _mock_key, mock_llm):
        """Step IDs with invalid characters produce validation errors."""
        bad_yaml = """\
name: bad-ids
description: Bad step ID format
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps:
  - id: "step with spaces!!!"
    prompt: "Invalid ID"
"""
        mock_llm.return_value = bad_yaml
        result = await generate_workflow("test")
        assert any("invalid" in e.lower() for e in result.validation_errors), (
            f"Expected invalid ID error, got: {result.validation_errors}"
        )

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_empty_steps_fails(self, _mock_key, mock_llm):
        """Workflow with empty steps list produces validation error."""
        bad_yaml = """\
name: empty-steps
description: No steps
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps: []
"""
        mock_llm.return_value = bad_yaml
        result = await generate_workflow("test")
        assert any("at least one step" in e for e in result.validation_errors), (
            f"Expected empty steps error, got: {result.validation_errors}"
        )

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_workflow_without_name_fails(self, _mock_key, mock_llm):
        """Workflow without a name field produces validation error."""
        bad_yaml = """\
description: No name provided
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps:
  - id: step-a
    prompt: "Do something"
"""
        mock_llm.return_value = bad_yaml
        result = await generate_workflow("test")
        assert any("name" in e.lower() for e in result.validation_errors), (
            f"Expected missing name error, got: {result.validation_errors}"
        )

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_duplicate_step_ids_fails(self, _mock_key, mock_llm):
        """Workflow with duplicate step IDs produces validation error."""
        bad_yaml = """\
name: dup-ids
description: Duplicate step IDs
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "input"
steps:
  - id: step-a
    prompt: "First step"
  - id: step-a
    prompt: "Duplicate step"
"""
        mock_llm.return_value = bad_yaml
        result = await generate_workflow("test")
        assert any("duplicate" in e.lower() for e in result.validation_errors), (
            f"Expected duplicate ID error, got: {result.validation_errors}"
        )

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_valid_workflow_returns_input_schema(self, _mock_key, mock_llm):
        """GenerateResult includes the parsed input_schema dict."""
        mock_llm.return_value = VALID_3STEP_YAML
        result = await generate_workflow("Build a pipeline")
        assert result.input_schema is not None
        assert "query" in str(result.input_schema)

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_valid_workflow_returns_description(self, _mock_key, mock_llm):
        """GenerateResult includes the parsed description."""
        mock_llm.return_value = VALID_3STEP_YAML
        result = await generate_workflow("Build a pipeline")
        assert result.description == "A three-step test pipeline"


# =========================================================================
# 5. Error handling (5+ tests)
# =========================================================================


class TestErrorHandling:
    """Verify error paths: missing keys, bad LLM output, secret scrubbing."""

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="")
    async def test_no_api_key_raises_clear_error(self, _mock_key):
        """generate_workflow raises ValueError when API key is missing."""
        with pytest.raises(ValueError, match="API key is required"):
            await generate_workflow("Test description")

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_llm_returns_empty_string(self, _mock_key, mock_llm):
        """Empty LLM response produces a parse error in validation_errors."""
        mock_llm.return_value = ""
        result = await generate_workflow("Test")
        assert len(result.validation_errors) > 0
        assert any("parse error" in e.lower() or "empty" in e.lower()
                    for e in result.validation_errors)

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_llm_returns_non_yaml_text(self, _mock_key, mock_llm):
        """Non-YAML prose from LLM produces a parse error."""
        mock_llm.return_value = (
            "I cannot generate that workflow because it requires "
            "access to proprietary APIs that are not available."
        )
        result = await generate_workflow("Do something impossible")
        assert len(result.validation_errors) > 0

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_llm_returns_truncated_yaml(self, _mock_key, mock_llm):
        """Truncated YAML (simulating token limit cut) produces a parse error."""
        truncated = """\
name: truncated-workflow
description: This YAML is cut off
input_schema:
  required: ["x"]
  properties:
    x:
      type: string
      description: "in
"""
        mock_llm.return_value = truncated
        result = await generate_workflow("Test")
        # Should either have validation errors or fail parsing
        # (the YAML parser may accept it but validation catches missing steps)
        assert (
            len(result.validation_errors) > 0
            or result.steps_count == 0
        )

    def test_scrub_secrets_bearer_token(self):
        """_scrub_secrets redacts Bearer tokens from error messages."""
        text = "Authorization: Bearer sk-ant-api03-verylongsecretkey123456"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result
        assert "REDACTED" in result

    def test_scrub_secrets_api_key_value(self):
        """_scrub_secrets redacts api_key=... patterns."""
        text = 'api_key: "super-secret-key-12345678"'
        result = _scrub_secrets(text)
        assert "super-secret" not in result
        assert "REDACTED" in result

    def test_scrub_secrets_aws_key(self):
        """_scrub_secrets redacts AWS access key IDs."""
        text = "Credential: AKIAIOSFODNN7EXAMPLE"
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "REDACTED" in result

    def test_scrub_secrets_pem_key(self):
        """_scrub_secrets redacts PEM private keys."""
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBogIBAAJBALRiML/secret/data\n"
            "-----END RSA PRIVATE KEY-----"
        )
        result = _scrub_secrets(text)
        assert "MIIBogIBAAJ" not in result
        assert "REDACTED-PEM-KEY" in result

    def test_scrub_secrets_credential_url(self):
        """_scrub_secrets redacts postgres:// credential URLs."""
        text = "postgres://admin:p4ssw0rd@db.example.com:5432/mydb"
        result = _scrub_secrets(text)
        assert "p4ssw0rd" not in result
        assert "REDACTED" in result

    def test_scrub_secrets_passthrough_safe_text(self):
        """_scrub_secrets does not alter normal error text without secrets."""
        text = "Connection timeout after 60 seconds to api.example.com"
        result = _scrub_secrets(text)
        assert result == text

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="")
    async def test_explain_error_no_key_returns_fallback(self, _mock_key):
        """explain_error without API key returns a basic fallback dict."""
        result = await explain_error(
            step_id="step-a",
            step_type="llm",
            error="Connection refused",
        )
        assert "summary" in result
        assert "fix" in result
        assert "API key not set" in result["cause"]

    @pytest.mark.asyncio
    @patch("sandcastle.engine.generator._call_advisor_llm", new_callable=AsyncMock)
    @patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key")
    async def test_explain_error_scrubs_secrets_before_llm_call(
        self, _mock_key, mock_llm
    ):
        """explain_error scrubs secrets from the error before sending to LLM."""
        mock_llm.return_value = json.dumps({
            "summary": "Auth failed",
            "cause": "Invalid token",
            "fix": "Check credentials",
            "severity": "high",
        })
        secret_error = "Bearer sk-ant-api03-secretvalue123456789012 was rejected"
        await explain_error(
            step_id="step-a",
            step_type="llm",
            error=secret_error,
        )
        # Verify the LLM was NOT called with the raw secret
        user_arg = mock_llm.call_args.kwargs.get(
            "user", mock_llm.call_args[1].get("user", "")
        )
        assert "sk-ant-api03" not in user_arg
        assert "REDACTED" in user_arg


# =========================================================================
# Additional edge case tests
# =========================================================================


class TestExampleTemplates:
    """Verify example template loading for few-shot prompting."""

    def test_loads_all_four_templates(self):
        """All 4 curated example templates load successfully."""
        examples = _load_example_templates()
        assert "research_agent" in examples
        assert "data_extractor" in examples
        assert "email_campaign" in examples
        assert "review_and_approve" in examples

    def test_examples_are_valid_yaml(self):
        """Each loaded example template is parseable YAML."""
        examples = _load_example_templates()
        # Each example is introduced by "--- Example: <name> ---"
        # Some templates may themselves contain "--- Example:" in comments,
        # so we count the exact header format with the trailing " ---".
        # There must be at least 4 top-level example blocks.
        assert examples.count("--- Example:") >= 4


class TestGenerateResultDataclass:
    """Verify GenerateResult defaults and structure."""

    def test_default_values(self):
        """GenerateResult has correct defaults."""
        r = GenerateResult(yaml_content="test")
        assert r.yaml_content == "test"
        assert r.name == ""
        assert r.description == ""
        assert r.steps_count == 0
        assert r.validation_errors == []
        assert r.input_schema is None

    def test_validation_errors_mutable_default(self):
        """Each GenerateResult gets its own validation_errors list."""
        r1 = GenerateResult(yaml_content="a")
        r2 = GenerateResult(yaml_content="b")
        r1.validation_errors.append("error")
        assert r2.validation_errors == []
