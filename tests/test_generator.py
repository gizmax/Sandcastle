"""Tests for the AI Workflow Generator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.generator import (
    GenerateResult,
    _build_system_prompt,
    _load_example_templates,
    _strip_fencing,
    generate_workflow,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_contains_known_models(self):
        """System prompt should list available model names."""
        prompt = _build_system_prompt()
        assert "sonnet" in prompt
        assert "haiku" in prompt
        assert "opus" in prompt

    def test_contains_variable_syntax(self):
        """System prompt should document variable syntax."""
        prompt = _build_system_prompt()
        assert "{input." in prompt
        assert "{steps." in prompt

    def test_contains_schema_docs(self):
        """System prompt should include YAML schema documentation."""
        prompt = _build_system_prompt()
        assert "input_schema" in prompt
        assert "depends_on" in prompt
        assert "kebab-case" in prompt


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


class TestTemplateLoading:
    def test_loads_example_templates(self):
        """Should load all four curated template examples."""
        examples = _load_example_templates()
        assert "research_agent" in examples
        assert "data_extractor" in examples
        assert "email_campaign" in examples
        assert "review_and_approve" in examples

    def test_examples_contain_yaml(self):
        """Loaded examples should contain actual YAML content."""
        examples = _load_example_templates()
        assert "steps:" in examples
        assert "input_schema:" in examples


# ---------------------------------------------------------------------------
# Fencing strip
# ---------------------------------------------------------------------------


class TestStripFencing:
    def test_strips_yaml_fence(self):
        """Should remove ```yaml ... ``` fencing."""
        text = "```yaml\nname: test\nsteps: []\n```"
        assert _strip_fencing(text) == "name: test\nsteps: []"

    def test_strips_plain_fence(self):
        """Should remove ``` ... ``` fencing without language tag."""
        text = "```\nname: test\n```"
        assert _strip_fencing(text) == "name: test"

    def test_no_fence_passthrough(self):
        """Should pass through text without fencing unchanged."""
        text = "name: test\nsteps: []"
        assert _strip_fencing(text) == text

    def test_strips_yml_fence(self):
        """Should remove ```yml ... ``` fencing."""
        text = "```yml\nname: test\n```"
        assert _strip_fencing(text) == "name: test"


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------


class TestApiKeyValidation:
    @pytest.mark.asyncio
    async def test_raises_without_api_key(self):
        """Should raise ValueError when no API key is configured."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=True), \
             patch("sandcastle.config.settings", MagicMock(anthropic_api_key="")):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                await generate_workflow("test description")


# ---------------------------------------------------------------------------
# Generation with mocked httpx
# ---------------------------------------------------------------------------

VALID_YAML = """\
name: test-workflow
description: A test workflow

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: ["topic"]
  properties:
    topic:
      type: string
      description: "Test topic"

steps:
  - id: step-one
    prompt: "Do something with {input.topic}"
    model: sonnet
    max_turns: 5
"""

INVALID_YAML = """\
name: bad-workflow
steps:
  - id: step-one
    prompt: test
    depends_on: [nonexistent]
"""

FENCED_YAML = f"```yaml\n{VALID_YAML}```"


def _mock_response(text: str):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "content": [{"text": text}],
    }
    return resp


class TestGenerateWorkflow:
    @pytest.mark.asyncio
    async def test_valid_yaml_returns_correct_result(self):
        """Valid YAML response should produce a correct GenerateResult."""
        mock_resp = _mock_response(VALID_YAML)

        with patch("sandcastle.engine.generator.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await generate_workflow("Create a test workflow")

        assert isinstance(result, GenerateResult)
        assert result.name == "test-workflow"
        assert result.steps_count == 1
        assert result.validation_errors == []
        assert result.input_schema is not None

    @pytest.mark.asyncio
    async def test_fenced_yaml_stripped(self):
        """Markdown-fenced YAML should be stripped before parsing."""
        mock_resp = _mock_response(FENCED_YAML)

        with patch("sandcastle.engine.generator.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await generate_workflow("Create a test workflow")

        assert result.name == "test-workflow"
        assert result.validation_errors == []

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_errors(self):
        """Invalid YAML should populate validation_errors."""
        mock_resp = _mock_response(INVALID_YAML)

        with patch("sandcastle.engine.generator.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await generate_workflow("Create a bad workflow")

        assert len(result.validation_errors) > 0

    @pytest.mark.asyncio
    async def test_refine_includes_existing_yaml(self):
        """Refine mode should include existing YAML in the user message."""
        mock_resp = _mock_response(VALID_YAML)

        with patch("sandcastle.engine.generator.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                await generate_workflow(
                    "Original description",
                    refine_from="name: old\nsteps: []",
                    refine_instruction="Add a review step",
                )

            # Verify the user message contained the existing YAML
            call_args = mock_client.post.call_args
            body = call_args[1]["json"] if "json" in call_args[1] else call_args.kwargs["json"]
            user_msg = body["messages"][0]["content"]
            assert "name: old" in user_msg
            assert "Add a review step" in user_msg


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCliIntegration:
    def test_generate_in_parser(self):
        """'generate' should be a valid CLI subcommand."""
        from sandcastle.__main__ import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["generate", "-d", "test workflow"])
        assert args.command == "generate"
        assert args.description == "test workflow"

    def test_generate_with_output(self):
        """'generate --output' should set the output file."""
        from sandcastle.__main__ import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["generate", "-d", "test", "-o", "out.yaml"])
        assert args.output == "out.yaml"

    def test_generate_in_dispatch(self):
        """'generate' should be in the dispatch dict."""
        from sandcastle.__main__ import _build_parser, main
        # Just verify the parser accepts it without error
        parser = _build_parser()
        args = parser.parse_args(["generate", "--refine", "-d", "test"])
        assert args.refine is True
