"""Tests for token waste detection and cache-aware execution (v0.29).

Covers output_max_tokens truncation, file read caching, prompt dedup
detection, token efficiency report generation, and YAML parsing.
"""

from __future__ import annotations

import hashlib
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    StepDefinition,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    WorkflowResult,
    _compute_token_report,
    resolve_storage_refs,
    resolve_templates,
    resolve_variable,
    _UNRESOLVED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**kwargs) -> RunContext:
    """Build a minimal RunContext for testing."""
    defaults = {
        "run_id": "test-run-001",
        "input": {},
    }
    defaults.update(kwargs)
    return RunContext(**defaults)


# ---------------------------------------------------------------------------
# 1. output_max_tokens field defaults to 0
# ---------------------------------------------------------------------------

class TestOutputMaxTokensDefault:
    def test_step_definition_default(self):
        """StepDefinition.output_max_tokens defaults to 0 (no limit)."""
        step = StepDefinition(id="test")
        assert step.output_max_tokens == 0

    def test_step_definition_custom_value(self):
        """output_max_tokens can be set to a positive value."""
        step = StepDefinition(id="test", output_max_tokens=500)
        assert step.output_max_tokens == 500


# ---------------------------------------------------------------------------
# 2. output_max_tokens truncates long output
# ---------------------------------------------------------------------------

class TestOutputMaxTokensTruncation:
    def test_truncate_long_string_output(self):
        """When output_max_tokens is set, long string outputs are truncated."""
        ctx = _make_context()
        ctx.step_outputs["research"] = "A" * 10000
        ctx._step_output_max_tokens = {"research": 500}

        # 500 tokens * 4 chars = 2000 chars max
        result = resolve_variable("steps.research.output", ctx)
        assert len(result) < 10000
        assert result.endswith("[... truncated to fit context window ...]")
        # First 2000 chars should be preserved
        assert result.startswith("A" * 2000)

    def test_no_truncation_when_disabled(self):
        """When output_max_tokens is 0, output is not truncated."""
        ctx = _make_context()
        long_output = "B" * 10000
        ctx.step_outputs["research"] = long_output
        ctx._step_output_max_tokens = {}

        result = resolve_variable("steps.research.output", ctx)
        assert result == long_output

    def test_no_truncation_for_short_output(self):
        """Short outputs are not truncated even with output_max_tokens set."""
        ctx = _make_context()
        ctx.step_outputs["research"] = "Short output"
        ctx._step_output_max_tokens = {"research": 500}

        result = resolve_variable("steps.research.output", ctx)
        assert result == "Short output"

    def test_truncation_in_resolve_templates_auto_inject(self):
        """Auto-injected dependency outputs are truncated when configured."""
        ctx = _make_context()
        ctx.step_outputs["data"] = "X" * 5000
        ctx._step_output_max_tokens = {"data": 100}

        # Template does NOT explicitly reference steps.data.output,
        # so it will be auto-injected
        result = resolve_templates(
            "Analyze the data.",
            ctx,
            depends_on=["data"],
        )
        # Auto-injected output should be truncated (100 tokens * 4 = 400 chars)
        assert "[... truncated to fit context window ...]" in result
        # Original 5000 chars should not all be present
        assert len(result) < 5000

    def test_dict_output_not_truncated(self):
        """Dict outputs are not truncated by resolve_variable (only strings)."""
        ctx = _make_context()
        ctx.step_outputs["research"] = {"key": "value" * 1000}
        ctx._step_output_max_tokens = {"research": 10}

        result = resolve_variable("steps.research.output", ctx)
        assert isinstance(result, dict)
        assert result == {"key": "value" * 1000}


# ---------------------------------------------------------------------------
# 3. File read cache returns cached value on second read
# ---------------------------------------------------------------------------

class TestFileReadCache:
    @pytest.mark.asyncio
    async def test_cached_value_returned(self):
        """Second read of same path returns cached content."""
        ctx = _make_context()
        storage = AsyncMock()
        storage.read = AsyncMock(return_value="file content here")

        # First read
        prompt1 = await resolve_storage_refs(
            "{storage.data/report.txt}", storage, ctx
        )
        assert prompt1 == "file content here"
        assert storage.read.call_count == 1
        assert "data/report.txt" in ctx._file_read_cache

        # Second read - should use cache
        storage.read.reset_mock()
        prompt2 = await resolve_storage_refs(
            "{storage.data/report.txt}", storage, ctx
        )
        assert prompt2 == "file content here"
        # storage.read should NOT be called again
        storage.read.assert_not_called()


# ---------------------------------------------------------------------------
# 4. File read counter increments correctly
# ---------------------------------------------------------------------------

class TestFileReadCounter:
    @pytest.mark.asyncio
    async def test_counter_increments(self):
        """File read count increments on each cached access."""
        ctx = _make_context()
        storage = AsyncMock()
        storage.read = AsyncMock(return_value="content")

        # First read - count = 1
        await resolve_storage_refs("{storage.file.txt}", storage, ctx)
        assert ctx._file_read_counts.get("file.txt") == 1

        # Second read - count = 2
        await resolve_storage_refs("{storage.file.txt}", storage, ctx)
        assert ctx._file_read_counts.get("file.txt") == 2

        # Third read - count = 3
        await resolve_storage_refs("{storage.file.txt}", storage, ctx)
        assert ctx._file_read_counts.get("file.txt") == 3


# ---------------------------------------------------------------------------
# 5. Duplicate file read warning logged
# ---------------------------------------------------------------------------

class TestFileReadWarning:
    @pytest.mark.asyncio
    async def test_warning_on_excessive_reads(self, caplog):
        """Warning is logged when a file is read more than 2 times."""
        ctx = _make_context()
        storage = AsyncMock()
        storage.read = AsyncMock(return_value="content")

        # Three reads to trigger warning (>2)
        for _ in range(3):
            await resolve_storage_refs("{storage.readme.md}", storage, ctx)

        with caplog.at_level(logging.WARNING, logger="sandcastle.engine.executor"):
            await resolve_storage_refs("{storage.readme.md}", storage, ctx)
        # The warning should have been logged
        assert any("read" in r.message and "readme.md" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 6. Prompt hash detection finds duplicates
# ---------------------------------------------------------------------------

class TestPromptDedup:
    def test_seen_prompt_hashes_tracks_hashes(self):
        """Prompt hashes are added to _seen_prompt_hashes."""
        ctx = _make_context()
        prompt = "Analyze the following data: some content"
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]

        # Simulate what the executor does
        ctx._seen_prompt_hashes.add(prompt_hash)
        assert prompt_hash in ctx._seen_prompt_hashes

    def test_duplicate_prompt_detected(self):
        """Same prompt hash is detected as duplicate."""
        ctx = _make_context()
        prompt = "Identical prompt content"
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]

        # First time - not a duplicate
        assert prompt_hash not in ctx._seen_prompt_hashes
        ctx._seen_prompt_hashes.add(prompt_hash)

        # Second time - IS a duplicate
        assert prompt_hash in ctx._seen_prompt_hashes

    def test_different_prompts_different_hashes(self):
        """Different prompts produce different hashes."""
        ctx = _make_context()
        hash1 = hashlib.md5("prompt A".encode()).hexdigest()[:12]
        hash2 = hashlib.md5("prompt B".encode()).hexdigest()[:12]

        ctx._seen_prompt_hashes.add(hash1)
        assert hash1 in ctx._seen_prompt_hashes
        assert hash2 not in ctx._seen_prompt_hashes


# ---------------------------------------------------------------------------
# 7. Token report generated after run
# ---------------------------------------------------------------------------

class TestTokenReport:
    def test_report_generated_with_empty_context(self):
        """Token report is generated even with no waste detected."""
        ctx = _make_context()
        report = _compute_token_report(ctx)

        assert isinstance(report, dict)
        assert "duplicate_file_reads" in report
        assert "recommendations" in report
        assert "unique_prompts" in report
        assert "cached_file_reads" in report
        assert report["duplicate_file_reads"] == {}
        assert report["recommendations"] == []

    def test_report_included_in_workflow_result(self):
        """WorkflowResult has token_report field."""
        result = WorkflowResult(
            run_id="test",
            outputs={},
            total_cost_usd=0.0,
            status="completed",
            token_report={"recommendations": []},
        )
        assert result.token_report is not None
        assert result.token_report["recommendations"] == []

    def test_report_defaults_to_none(self):
        """WorkflowResult.token_report defaults to None."""
        result = WorkflowResult(
            run_id="test",
            outputs={},
            total_cost_usd=0.0,
            status="completed",
        )
        assert result.token_report is None


# ---------------------------------------------------------------------------
# 8. Token report recommendations for large outputs
# ---------------------------------------------------------------------------

class TestTokenReportLargeOutputs:
    def test_large_string_output_recommendation(self):
        """Report recommends output_max_tokens for large string outputs."""
        ctx = _make_context()
        ctx.step_outputs["research"] = "A" * 25000  # > 20000 chars

        report = _compute_token_report(ctx)
        assert len(report["recommendations"]) >= 1
        assert any("research" in r and "output_max_tokens" in r for r in report["recommendations"])

    def test_large_dict_output_recommendation(self):
        """Report recommends output_max_tokens for large dict outputs."""
        ctx = _make_context()
        ctx.step_outputs["data"] = {"content": "B" * 25000}

        report = _compute_token_report(ctx)
        assert len(report["recommendations"]) >= 1
        assert any("data" in r and "output_max_tokens" in r for r in report["recommendations"])

    def test_small_output_no_recommendation(self):
        """No recommendation for small outputs."""
        ctx = _make_context()
        ctx.step_outputs["quick"] = "Short result"

        report = _compute_token_report(ctx)
        assert not any("quick" in r for r in report["recommendations"])


# ---------------------------------------------------------------------------
# 9. Token report recommendations for duplicate reads
# ---------------------------------------------------------------------------

class TestTokenReportDuplicateReads:
    def test_duplicate_reads_in_report(self):
        """Duplicate file reads appear in the report."""
        ctx = _make_context()
        ctx._file_read_counts = {"data/config.yaml": 3, "data/schema.json": 1}

        report = _compute_token_report(ctx)
        assert "data/config.yaml" in report["duplicate_file_reads"]
        assert report["duplicate_file_reads"]["data/config.yaml"] == 3
        # Single read should not be in duplicates
        assert "data/schema.json" not in report["duplicate_file_reads"]

    def test_duplicate_reads_generate_recommendation(self):
        """Duplicate file reads generate recommendations."""
        ctx = _make_context()
        ctx._file_read_counts = {"report.pdf": 5}
        ctx._file_read_cache = {"report.pdf": "cached content"}

        report = _compute_token_report(ctx)
        assert any("report.pdf" in r and "5 times" in r for r in report["recommendations"])

    def test_cached_file_count(self):
        """Report includes count of cached files."""
        ctx = _make_context()
        ctx._file_read_cache = {"a.txt": "aaa", "b.txt": "bbb"}

        report = _compute_token_report(ctx)
        assert report["cached_file_reads"] == 2


# ---------------------------------------------------------------------------
# 10. output_max_tokens in YAML parsing
# ---------------------------------------------------------------------------

# Use quoted strings to avoid YAML interpreting curly braces
YAML_WITH_OUTPUT_MAX_TOKENS = (
    "name: token-opt-test\n"
    "description: Test output_max_tokens parsing\n"
    "\n"
    "default_model: sonnet\n"
    "default_max_turns: 10\n"
    "default_timeout: 300\n"
    "\n"
    "steps:\n"
    "  - id: research\n"
    "    output_max_tokens: 500\n"
    "    prompt: Research the topic thoroughly.\n"
    "\n"
    "  - id: summarize\n"
    "    depends_on: [research]\n"
    "    output_max_tokens: 200\n"
    '    prompt: "Summarize: {steps.research.output}"\n'
    "\n"
    "  - id: report\n"
    "    depends_on: [summarize]\n"
    "    prompt: Write final report.\n"
)

YAML_WITHOUT_OUTPUT_MAX_TOKENS = (
    "name: basic-test\n"
    "description: Test without output_max_tokens\n"
    "\n"
    "default_model: sonnet\n"
    "default_max_turns: 10\n"
    "default_timeout: 300\n"
    "\n"
    "steps:\n"
    "  - id: analyze\n"
    "    prompt: Analyze data.\n"
)


class TestYamlParsing:
    def test_output_max_tokens_parsed(self):
        """output_max_tokens is correctly parsed from YAML."""
        wf = parse_yaml_string(YAML_WITH_OUTPUT_MAX_TOKENS)
        errors = validate(wf)
        assert not errors, f"Validation errors: {errors}"

        research = wf.get_step("research")
        assert research.output_max_tokens == 500

        summarize = wf.get_step("summarize")
        assert summarize.output_max_tokens == 200

    def test_output_max_tokens_defaults_in_yaml(self):
        """Steps without output_max_tokens default to 0."""
        wf = parse_yaml_string(YAML_WITHOUT_OUTPUT_MAX_TOKENS)
        errors = validate(wf)
        assert not errors, f"Validation errors: {errors}"

        analyze = wf.get_step("analyze")
        assert analyze.output_max_tokens == 0

    def test_report_step_no_output_max_tokens(self):
        """Steps in YAML that omit output_max_tokens get default 0."""
        wf = parse_yaml_string(YAML_WITH_OUTPUT_MAX_TOKENS)
        report_step = wf.get_step("report")
        assert report_step.output_max_tokens == 0


# ---------------------------------------------------------------------------
# 11. RunContext propagation in with_item
# ---------------------------------------------------------------------------

class TestContextPropagation:
    def test_with_item_propagates_cache_fields(self):
        """with_item copies file cache, read counts, and prompt hashes."""
        ctx = _make_context()
        ctx._file_read_cache = {"a.txt": "content"}
        ctx._file_read_counts = {"a.txt": 2}
        ctx._seen_prompt_hashes = {"abc123"}
        ctx._step_output_max_tokens = {"research": 500}

        child = ctx.with_item("item", 0)

        assert child._file_read_cache == {"a.txt": "content"}
        assert child._file_read_counts == {"a.txt": 2}
        assert child._seen_prompt_hashes == {"abc123"}
        assert child._step_output_max_tokens == {"research": 500}

    def test_with_item_isolates_mutations(self):
        """Child context mutations don't affect parent."""
        ctx = _make_context()
        ctx._file_read_cache = {}
        ctx._seen_prompt_hashes = set()

        child = ctx.with_item("item", 0)
        child._file_read_cache["new.txt"] = "data"
        child._seen_prompt_hashes.add("xyz")

        assert "new.txt" not in ctx._file_read_cache
        assert "xyz" not in ctx._seen_prompt_hashes


# ---------------------------------------------------------------------------
# 12. resolve_storage_refs without context (backward compatibility)
# ---------------------------------------------------------------------------

class TestStorageRefsBackwardCompat:
    @pytest.mark.asyncio
    async def test_works_without_context(self):
        """resolve_storage_refs still works when context is None."""
        storage = AsyncMock()
        storage.read = AsyncMock(return_value="resolved value")

        result = await resolve_storage_refs(
            "{storage.path/to/file}", storage
        )
        assert result == "resolved value"
        storage.read.assert_called_once_with("path/to/file")

    @pytest.mark.asyncio
    async def test_no_refs_returns_prompt_unchanged(self):
        """Prompts without storage refs are returned unchanged."""
        storage = AsyncMock()
        result = await resolve_storage_refs(
            "Just a plain prompt.", storage
        )
        assert result == "Just a plain prompt."
        storage.read.assert_not_called()


# ---------------------------------------------------------------------------
# 13. Token report with prompt hashes
# ---------------------------------------------------------------------------

class TestTokenReportPromptHashes:
    def test_unique_prompts_count(self):
        """Report tracks the number of unique prompts."""
        ctx = _make_context()
        ctx._seen_prompt_hashes = {"aaa", "bbb", "ccc"}

        report = _compute_token_report(ctx)
        assert report["unique_prompts"] == 3

    def test_empty_hashes(self):
        """No prompts results in unique_prompts = 0."""
        ctx = _make_context()
        report = _compute_token_report(ctx)
        assert report["unique_prompts"] == 0


# ---------------------------------------------------------------------------
# 14. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_truncation_boundary_exact(self):
        """Output exactly at the limit is not truncated."""
        ctx = _make_context()
        # 100 tokens * 4 chars = 400 chars exactly
        ctx.step_outputs["step1"] = "A" * 400
        ctx._step_output_max_tokens = {"step1": 100}

        result = resolve_variable("steps.step1.output", ctx)
        assert result == "A" * 400
        assert "[... truncated" not in result

    def test_truncation_one_char_over(self):
        """Output one char over the limit is truncated."""
        ctx = _make_context()
        ctx.step_outputs["step1"] = "A" * 401
        ctx._step_output_max_tokens = {"step1": 100}

        result = resolve_variable("steps.step1.output", ctx)
        assert "[... truncated to fit context window ...]" in result
        assert result.startswith("A" * 400)

    def test_none_output_not_affected(self):
        """None outputs are not affected by truncation."""
        ctx = _make_context()
        ctx.step_outputs["step1"] = None
        ctx._step_output_max_tokens = {"step1": 100}

        result = resolve_variable("steps.step1.output", ctx)
        assert result is None

    def test_nested_field_access_unaffected(self):
        """Accessing specific fields (steps.X.output.field) bypasses truncation."""
        ctx = _make_context()
        ctx.step_outputs["step1"] = {"title": "Test", "body": "B" * 10000}
        ctx._step_output_max_tokens = {"step1": 10}

        # Accessing a specific field should work normally
        result = resolve_variable("steps.step1.output.title", ctx)
        assert result == "Test"

    @pytest.mark.asyncio
    async def test_file_read_cache_none_content(self):
        """Storage returning None is not cached."""
        ctx = _make_context()
        storage = AsyncMock()
        storage.read = AsyncMock(return_value=None)

        result = await resolve_storage_refs(
            "{storage.missing.txt}", storage, ctx
        )
        # Placeholder should be returned
        assert result == "{storage.missing.txt}"
        # None should NOT be cached
        assert "missing.txt" not in ctx._file_read_cache


# ---------------------------------------------------------------------------
# 15. API schema includes token_report
# ---------------------------------------------------------------------------

class TestApiSchema:
    def test_run_status_response_has_token_report(self):
        """RunStatusResponse schema includes token_report field."""
        from sandcastle.api.schemas import RunStatusResponse
        fields = RunStatusResponse.model_fields
        assert "token_report" in fields
        # Default should be None
        resp = RunStatusResponse(
            run_id="test",
            workflow_name="wf",
            status="completed",
        )
        assert resp.token_report is None

    def test_run_status_response_with_token_report(self):
        """RunStatusResponse accepts token_report data."""
        from sandcastle.api.schemas import RunStatusResponse
        report = {
            "duplicate_file_reads": {},
            "recommendations": ["Step 'x' output is large"],
            "unique_prompts": 3,
            "cached_file_reads": 1,
        }
        resp = RunStatusResponse(
            run_id="test",
            workflow_name="wf",
            status="completed",
            token_report=report,
        )
        assert resp.token_report == report
        assert len(resp.token_report["recommendations"]) == 1


# ---------------------------------------------------------------------------
# 16. Combined report - full workflow scenario
# ---------------------------------------------------------------------------

class TestCombinedReport:
    def test_full_scenario_report(self):
        """Token report with multiple waste indicators produces combined recommendations."""
        ctx = _make_context()
        # Large output from step
        ctx.step_outputs["scrape"] = "D" * 30000
        ctx.step_outputs["analyze"] = "Short result"
        # Duplicate file reads
        ctx._file_read_counts = {"config.yaml": 4, "data.json": 1}
        ctx._file_read_cache = {"config.yaml": "cached", "data.json": "cached"}
        # Some prompt hashes
        ctx._seen_prompt_hashes = {"hash1", "hash2", "hash3"}

        report = _compute_token_report(ctx)

        # Should have recommendations for large output + duplicate reads
        assert len(report["recommendations"]) >= 2
        assert any("scrape" in r for r in report["recommendations"])
        assert any("config.yaml" in r for r in report["recommendations"])
        # Small step should NOT generate a recommendation
        assert not any("analyze" in r for r in report["recommendations"])
        # Metadata
        assert report["unique_prompts"] == 3
        assert report["cached_file_reads"] == 2
        assert report["duplicate_file_reads"] == {"config.yaml": 4}
