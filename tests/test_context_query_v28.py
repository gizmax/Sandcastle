"""Tests for dynamic context_query feature on workflow steps (v0.28).

Covers StepDefinition parsing, context_source validation, context
resolution dispatching (memory, web, files, custom), truncation,
template variable injection, and backward compatibility.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    VALID_CONTEXT_SOURCES,
    StepDefinition,
    parse_yaml_string,
    validate,
)


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

WORKFLOW_WITH_CONTEXT_QUERY = """\
name: test-context-query
description: Workflow testing context_query feature

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: [topic]
  properties:
    topic:
      type: string
      description: The topic to research

steps:
  - id: research
    context_query: "latest trends in {input.topic}"
    context_source: web
    context_max_tokens: 3000
    prompt: |
      Based on this research:
      {context}

      Write a comprehensive analysis of {input.topic}.

  - id: summarize
    depends_on: [research]
    context_query: "summary best practices"
    context_source: memory
    prompt: |
      Using this background:
      {context}

      Summarize: {steps.research.output}
"""

WORKFLOW_WITHOUT_CONTEXT_QUERY = """\
name: test-no-context
description: Workflow without context_query (backward compat)

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: [data]
  properties:
    data:
      type: string

steps:
  - id: process
    prompt: "Process the data: {input.data}"
"""

WORKFLOW_FILES_SOURCE = """\
name: test-files-source
description: Workflow with files context source

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: [query]
  properties:
    query:
      type: string

steps:
  - id: search-files
    context_query: "data pipeline"
    context_source: files
    context_max_tokens: 1500
    prompt: |
      Using these workflow references:
      {context}

      Build a similar workflow for {input.query}.
"""

WORKFLOW_CUSTOM_SOURCE = """\
name: test-custom-source
description: Workflow with custom context source

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: []
  properties: {}

steps:
  - id: env-context
    context_query: "echo hello world"
    context_source: custom
    context_max_tokens: 500
    prompt: |
      System info:
      {context}

      Describe the environment.
"""

WORKFLOW_INVALID_SOURCE = """\
name: test-bad-source
description: Workflow with invalid context source

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: []
  properties: {}

steps:
  - id: bad
    context_query: "something"
    context_source: invalid_source
    prompt: "Test"
"""

WORKFLOW_DEFAULTS_ONLY = """\
name: test-defaults
description: Workflow with context_query but default source and max_tokens

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: []
  properties: {}

steps:
  - id: default-ctx
    context_query: "search for something"
    prompt: |
      Context: {context}
      Do the task.
"""


# ---------------------------------------------------------------------------
# StepDefinition parsing tests
# ---------------------------------------------------------------------------


class TestStepDefinitionContextFields:
    """Tests for context_query fields on StepDefinition."""

    def test_context_query_parsed_from_yaml(self):
        """context_query, context_source, context_max_tokens are parsed."""
        wf = parse_yaml_string(WORKFLOW_WITH_CONTEXT_QUERY)
        step = wf.steps[0]
        assert step.context_query == "latest trends in {input.topic}"
        assert step.context_source == "web"
        assert step.context_max_tokens == 3000

    def test_context_query_second_step(self):
        """Second step also has context_query parsed correctly."""
        wf = parse_yaml_string(WORKFLOW_WITH_CONTEXT_QUERY)
        step = wf.steps[1]
        assert step.context_query == "summary best practices"
        assert step.context_source == "memory"
        assert step.context_max_tokens == 2000  # default

    def test_no_context_query_backward_compat(self):
        """Steps without context_query default to empty string."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_CONTEXT_QUERY)
        step = wf.steps[0]
        assert step.context_query == ""
        assert step.context_source == "memory"  # default
        assert step.context_max_tokens == 2000  # default

    def test_context_max_tokens_default(self):
        """context_max_tokens defaults to 2000."""
        wf = parse_yaml_string(WORKFLOW_DEFAULTS_ONLY)
        step = wf.steps[0]
        assert step.context_max_tokens == 2000

    def test_context_source_defaults_to_memory(self):
        """context_source defaults to 'memory' when not specified."""
        wf = parse_yaml_string(WORKFLOW_DEFAULTS_ONLY)
        step = wf.steps[0]
        assert step.context_source == "memory"

    def test_context_source_files(self):
        """context_source 'files' is valid."""
        wf = parse_yaml_string(WORKFLOW_FILES_SOURCE)
        step = wf.steps[0]
        assert step.context_source == "files"

    def test_context_source_custom(self):
        """context_source 'custom' is valid."""
        wf = parse_yaml_string(WORKFLOW_CUSTOM_SOURCE)
        step = wf.steps[0]
        assert step.context_source == "custom"

    def test_invalid_context_source_rejected(self):
        """Invalid context_source raises ValueError during parsing."""
        with pytest.raises(ValueError, match="Invalid context_source"):
            parse_yaml_string(WORKFLOW_INVALID_SOURCE)

    def test_valid_context_sources_constant(self):
        """VALID_CONTEXT_SOURCES contains all expected values."""
        assert VALID_CONTEXT_SOURCES == {"memory", "web", "files", "custom"}

    def test_workflow_validates_with_context_query(self):
        """Workflow with context_query passes validation."""
        wf = parse_yaml_string(WORKFLOW_WITH_CONTEXT_QUERY)
        errors = validate(wf)
        assert errors == []

    def test_workflow_validates_without_context_query(self):
        """Workflow without context_query still passes validation."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_CONTEXT_QUERY)
        errors = validate(wf)
        assert errors == []

    def test_step_definition_dataclass_defaults(self):
        """StepDefinition created directly has correct context defaults."""
        step = StepDefinition(id="test", prompt="Test prompt")
        assert step.context_query == ""
        assert step.context_source == "memory"
        assert step.context_max_tokens == 2000


# ---------------------------------------------------------------------------
# Context resolution tests (executor functions)
# ---------------------------------------------------------------------------


class TestResolveContextQuery:
    """Tests for _resolve_context_query and related functions."""

    def _make_context(self, **kwargs):
        """Create a minimal RunContext for testing."""
        from sandcastle.engine.executor import RunContext

        defaults = {
            "run_id": "test-run-123",
            "input": {"topic": "AI quantization"},
            "step_outputs": {},
            "workflow_name": "test-wf",
            "admin_trusted": True,
        }
        defaults.update(kwargs)
        return RunContext(**defaults)

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        """Empty context_query returns empty string."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(id="test", prompt="Test", context_query="")
        ctx = self._make_context()
        result = await _resolve_context_query(step, ctx)
        assert result == ""

    @pytest.mark.asyncio
    async def test_memory_source_calls_load_memories(self):
        """Memory source dispatches to _context_search_memory."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="AI trends",
            context_source="memory",
        )
        ctx = self._make_context()

        with patch(
            "sandcastle.engine.executor._context_search_memory",
            new_callable=AsyncMock,
            return_value="Memory result about AI trends",
        ) as mock_mem:
            result = await _resolve_context_query(step, ctx)
            assert "Memory result about AI trends" in result
            mock_mem.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_web_source_calls_web_search(self):
        """Web source dispatches to _context_search_web."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="latest AI news",
            context_source="web",
        )
        ctx = self._make_context()

        with patch(
            "sandcastle.engine.executor._context_search_web",
            new_callable=AsyncMock,
            return_value="Web search results about AI",
        ) as mock_web:
            result = await _resolve_context_query(step, ctx)
            assert "Web search results about AI" in result
            mock_web.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_files_source_calls_file_search(self):
        """Files source dispatches to _context_search_files."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="data pipeline",
            context_source="files",
        )
        ctx = self._make_context()

        with patch(
            "sandcastle.engine.executor._context_search_files",
            return_value="File search results",
        ) as mock_files:
            result = await _resolve_context_query(step, ctx)
            assert "File search results" in result
            mock_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_source_calls_command(self):
        """Custom source dispatches to _context_run_command."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="echo hello",
            context_source="custom",
        )
        ctx = self._make_context()

        with patch(
            "sandcastle.engine.executor._context_run_command",
            new_callable=AsyncMock,
            return_value="hello\n",
        ) as mock_cmd:
            result = await _resolve_context_query(step, ctx)
            assert "hello" in result
            mock_cmd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_truncation_at_max_tokens(self):
        """Result is truncated when exceeding context_max_tokens."""
        from sandcastle.engine.executor import _resolve_context_query

        # 100 tokens ~ 400 chars; generate 2000 chars
        long_text = "word " * 400  # 2000 chars

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="something",
            context_source="memory",
            context_max_tokens=100,  # ~400 chars
        )
        ctx = self._make_context()

        with patch(
            "sandcastle.engine.executor._context_search_memory",
            new_callable=AsyncMock,
            return_value=long_text,
        ):
            result = await _resolve_context_query(step, ctx)
            # Should be truncated: 100 tokens * 4 chars = 400 chars + truncation marker
            assert len(result) < len(long_text)
            # The two truncation markers the engine used to have ("...[truncated]"
            # on this path, "[... truncated to fit context window ...]" on the
            # output_max_tokens path) were unified when compaction landed.
            assert result.endswith("[... truncated to fit context window ...]")

    @pytest.mark.asyncio
    async def test_template_variables_resolved_in_query(self):
        """Template variables in context_query are resolved before search."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="trends in {input.topic}",
            context_source="memory",
        )
        ctx = self._make_context(input={"topic": "quantum computing"})

        with patch(
            "sandcastle.engine.executor._context_search_memory",
            new_callable=AsyncMock,
            return_value="quantum results",
        ) as mock_mem:
            result = await _resolve_context_query(step, ctx)
            # The query passed to memory search should have {input.topic} resolved
            call_args = mock_mem.call_args
            assert "quantum computing" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty(self):
        """When the source returns empty, result is empty string."""
        from sandcastle.engine.executor import _resolve_context_query

        step = StepDefinition(
            id="test",
            prompt="Test",
            context_query="nothing here",
            context_source="memory",
        )
        ctx = self._make_context()

        with patch(
            "sandcastle.engine.executor._context_search_memory",
            new_callable=AsyncMock,
            return_value="",
        ):
            result = await _resolve_context_query(step, ctx)
            assert result == ""


# ---------------------------------------------------------------------------
# Truncation function tests
# ---------------------------------------------------------------------------


class TestTruncateToTokens:
    """Tests for _truncate_to_tokens helper."""

    def test_short_text_not_truncated(self):
        from sandcastle.engine.executor import _truncate_to_tokens

        text = "short text"
        result = _truncate_to_tokens(text, 100)
        assert result == text

    def test_long_text_truncated(self):
        from sandcastle.engine.executor import _truncate_to_tokens

        text = "x" * 1000
        result = _truncate_to_tokens(text, 50)  # 50 tokens ~ 200 chars
        assert len(result) < 1000
        assert result.endswith("...[truncated]")

    def test_exact_boundary(self):
        from sandcastle.engine.executor import _truncate_to_tokens

        text = "x" * 400  # exactly 100 tokens worth
        result = _truncate_to_tokens(text, 100)
        assert result == text  # should not truncate at exact boundary


# ---------------------------------------------------------------------------
# Context variable in templates
# ---------------------------------------------------------------------------


class TestContextTemplateVariable:
    """Tests for {context} template variable in resolve_templates."""

    def _make_context(self, resolved_context="", **kwargs):
        from sandcastle.engine.executor import RunContext

        defaults = {
            "run_id": "test-123",
            "input": {},
            "step_outputs": {},
            "workflow_name": "test",
            "_resolved_context": resolved_context,
        }
        defaults.update(kwargs)
        return RunContext(**defaults)

    def test_context_variable_resolved(self):
        """The {context} variable is replaced with _resolved_context."""
        from sandcastle.engine.executor import resolve_templates

        ctx = self._make_context(resolved_context="This is dynamic context")
        result = resolve_templates("Background:\n{context}\n\nTask: do something", ctx)
        assert "This is dynamic context" in result

    def test_context_variable_empty(self):
        """When _resolved_context is empty, {context} resolves to empty string."""
        from sandcastle.engine.executor import resolve_templates

        ctx = self._make_context(resolved_context="")
        result = resolve_templates("Info: {context}\nEnd", ctx)
        assert "Info: \nEnd" in result

    def test_context_variable_with_other_vars(self):
        """{context} works alongside {input.*} and other variables."""
        from sandcastle.engine.executor import resolve_templates

        ctx = self._make_context(
            resolved_context="Some background info",
            input={"topic": "AI"},
        )
        result = resolve_templates(
            "Topic: {input.topic}\nContext: {context}\nDone", ctx
        )
        assert "Topic: AI" in result
        assert "Context: Some background info" in result

    def test_context_variable_not_in_regex_when_absent(self):
        """Prompt without {context} is unaffected by _resolved_context."""
        from sandcastle.engine.executor import resolve_templates

        ctx = self._make_context(resolved_context="should not appear")
        result = resolve_templates("Just a normal prompt with {run_id}", ctx)
        assert "should not appear" not in result
        assert "test-123" in result


# ---------------------------------------------------------------------------
# Context search helpers - unit tests
# ---------------------------------------------------------------------------


class TestContextSearchFiles:
    """Tests for _context_search_files helper."""

    def test_keyword_overlap_ranking(self):
        """Files with more keyword overlap rank higher."""
        from sandcastle.engine.executor import _context_search_files

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two workflow files
            Path(tmpdir, "pipeline.yaml").write_text(
                "name: data-pipeline\ndescription: A data pipeline workflow\n"
                "steps:\n  - id: extract\n    prompt: Extract data\n"
            )
            Path(tmpdir, "report.yaml").write_text(
                "name: weekly-report\ndescription: Generate a weekly report\n"
                "steps:\n  - id: write\n    prompt: Write report\n"
            )

            with patch("sandcastle.config.settings") as mock_settings:
                mock_settings.workflows_dir = tmpdir
                result = _context_search_files("data pipeline extract", limit=5)
                # pipeline.yaml should match (has "data", "pipeline", "extract")
                assert "data-pipeline" in result

    def test_no_workflows_dir(self):
        """Returns empty when workflows dir does not exist."""
        from sandcastle.engine.executor import _context_search_files

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent/path"
            result = _context_search_files("test query")
            assert result == ""


class TestContextRunCommand:
    """Tests for _context_run_command helper."""

    @pytest.mark.asyncio
    async def test_successful_command(self):
        """Successful shell command returns stdout."""
        from sandcastle.engine.executor import _context_run_command

        result = await _context_run_command("echo hello world")
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_failed_command_returns_empty(self):
        """Failed command returns empty string."""
        from sandcastle.engine.executor import _context_run_command

        result = await _context_run_command("exit 1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        """Command that exceeds timeout returns empty string."""
        from sandcastle.engine.executor import _context_run_command

        with patch("sandcastle.engine.executor.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            # Patch at module level since _context_run_command uses asyncio.wait_for
            result = await _context_run_command("sleep 999")
            assert result == ""


class TestContextSearchWeb:
    """Tests for _context_search_web helper."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self):
        """Without TOOL_TAVILY_API_KEY, returns empty."""
        from sandcastle.engine.executor import _context_search_web

        with patch.dict("os.environ", {}, clear=True):
            result = await _context_search_web("test query")
            assert result == ""

    @pytest.mark.asyncio
    async def test_with_api_key_makes_request(self):
        """With API key set, makes HTTP request to Tavily."""
        from sandcastle.engine.executor import _context_search_web

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Result 1", "content": "Content about AI trends"},
                {"title": "Result 2", "content": "More AI content"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with (
            patch.dict("os.environ", {"TOOL_TAVILY_API_KEY": "test-key"}),
            patch.dict("sys.modules", {"httpx": mock_httpx}),
        ):
            result = await _context_search_web("AI trends")
            assert "Content about AI trends" in result
            assert "More AI content" in result


class TestContextSearchMemory:
    """Tests for _context_search_memory helper."""

    @pytest.mark.asyncio
    async def test_memory_search_returns_content(self):
        """Memory search returns concatenated memory content."""
        from sandcastle.engine.executor import _context_search_memory

        mock_memories = [
            {"id": "1", "memory": "AI models are getting smaller"},
            {"id": "2", "memory": "Quantization reduces model size"},
        ]

        with patch(
            "sandcastle.engine.memory.load_memories",
            new_callable=AsyncMock,
            return_value=mock_memories,
        ):
            result = await _context_search_memory("AI quantization", "test-scope")
            assert "AI models are getting smaller" in result
            assert "Quantization reduces model size" in result

    @pytest.mark.asyncio
    async def test_memory_search_empty_results(self):
        """Memory search with no results returns empty string."""
        from sandcastle.engine.executor import _context_search_memory

        with patch(
            "sandcastle.engine.memory.load_memories",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await _context_search_memory("nothing", "test-scope")
            assert result == ""

    @pytest.mark.asyncio
    async def test_memory_search_error_returns_empty(self):
        """Memory search failure returns empty string gracefully."""
        from sandcastle.engine.executor import _context_search_memory

        with patch(
            "sandcastle.engine.memory.load_memories",
            new_callable=AsyncMock,
            side_effect=RuntimeError("mem0 unavailable"),
        ):
            result = await _context_search_memory("query", "scope")
            assert result == ""


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------


class TestContextQueryIntegration:
    """Higher-level tests combining parsing + resolution."""

    def test_workflow_with_all_sources_parses(self):
        """A workflow using all four context sources parses correctly."""
        yaml_content = """\
name: multi-source
description: Uses all context sources

default_model: sonnet

input_schema:
  required: []
  properties: {}

steps:
  - id: mem-step
    context_query: "search memory"
    context_source: memory
    prompt: "Use {context}"

  - id: web-step
    context_query: "search web"
    context_source: web
    context_max_tokens: 5000
    prompt: "Use {context}"

  - id: files-step
    context_query: "search files"
    context_source: files
    prompt: "Use {context}"

  - id: custom-step
    context_query: "uname -a"
    context_source: custom
    context_max_tokens: 500
    prompt: "System: {context}"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert errors == []
        assert len(wf.steps) == 4

        assert wf.steps[0].context_source == "memory"
        assert wf.steps[1].context_source == "web"
        assert wf.steps[1].context_max_tokens == 5000
        assert wf.steps[2].context_source == "files"
        assert wf.steps[3].context_source == "custom"
        assert wf.steps[3].context_max_tokens == 500

    def test_context_query_with_template_vars_in_yaml(self):
        """context_query containing {input.X} template variables is stored raw."""
        yaml_content = """\
name: template-query
description: Query with templates

default_model: sonnet

input_schema:
  required: [topic]
  properties:
    topic:
      type: string

steps:
  - id: research
    context_query: "latest news about {input.topic} in 2026"
    context_source: web
    prompt: "Research: {context}"
"""
        wf = parse_yaml_string(yaml_content)
        step = wf.steps[0]
        # The raw template is preserved - resolution happens at runtime
        assert "{input.topic}" in step.context_query
