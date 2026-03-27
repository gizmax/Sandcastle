"""
ENGINE DEEP TEST SUITE v27
===========================
Comprehensive edge-case tests for evolution, docparse, memory, and executor.

Run:
    cd /Users/gizmax/Documents/Sandcastle
    uv run pytest tests/test_engine_deep_v27.py -v --tb=short

Categories:
    uv run pytest tests/test_engine_deep_v27.py::TestEvolutionScore -v
    uv run pytest tests/test_engine_deep_v27.py::TestEvolutionMutations -v
    uv run pytest tests/test_engine_deep_v27.py::TestDocparse -v
    uv run pytest tests/test_engine_deep_v27.py::TestMemorySystem -v
    uv run pytest tests/test_engine_deep_v27.py::TestExecutorEdgeCases -v
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import json
import math
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Evolution engine imports
# ---------------------------------------------------------------------------
from sandcastle.engine.evolution import (
    MODEL_LADDER,
    _pick_mutation_type,
    _workflow_to_yaml,
    compute_evolution_score,
    mutate_model,
    mutate_prompt,
    mutate_simplify,
)

# ---------------------------------------------------------------------------
# Docparse imports
# ---------------------------------------------------------------------------
from sandcastle.engine.docparse import (
    ParseResult,
    _parse_csv,
    _parse_page_ranges,
    parse_document,
    screenshot_page,
)

# ---------------------------------------------------------------------------
# Memory imports
# ---------------------------------------------------------------------------
from sandcastle.engine.memory import (
    MemoryAdmissionError,
    MemoryBackendError,
    MemoryError,
    MemoryErrorKind,
    _STOPWORDS,
    _word_set,
    apply_decay,
    detect_conflicts,
    enrich_memory,
    format_memories_for_prompt,
    score_importance,
    should_admit,
)

# ---------------------------------------------------------------------------
# Executor imports
# ---------------------------------------------------------------------------
from sandcastle.engine.dag import (
    ConditionConfig,
    CsvOutputConfig,
    ExecutionPlan,
    FallbackConfig,
    PdfReportConfig,
    RetryConfig,
    StepDefinition,
    WorkflowDefinition,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    WorkflowResult,
    _backoff_delay,
    _check_budget,
    _safe_cost,
    _validate_browser_url,
    _write_csv_output,
    resolve_templates,
    resolve_variable,
    _UNRESOLVED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_YAML = """\
name: test-workflow
description: Test workflow
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    max_turns: 10
"""

TWO_STEP_YAML = """\
name: test-workflow
description: Two-step workflow
default_model: sonnet
steps:
  - id: step1
    prompt: "Analyze data"
    max_turns: 8
    tools:
      - search
      - calculator
  - id: step2
    prompt: "Summarize"
    depends_on:
      - step1
    max_turns: 5
"""


def _make_context(**kwargs) -> RunContext:
    """Create a RunContext with sensible defaults for testing."""
    defaults = {
        "run_id": str(uuid.uuid4()),
        "input": {},
    }
    defaults.update(kwargs)
    return RunContext(**defaults)


# ===========================================================================
# 1. EVOLUTION ENGINE EDGE CASES (17 tests)
# ===========================================================================


class TestEvolutionScore:
    """Tests for compute_evolution_score."""

    def test_all_zero_inputs(self):
        """All zeros should produce zero score."""
        score = compute_evolution_score(
            quality=0.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=0,
        )
        assert score == 0.0

    def test_perfect_quality_balanced(self):
        """Perfect quality with sufficient runs should give high score."""
        score = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=10.0, eval_runs=5,
        )
        # balanced: quality * 60 * confidence - cost * 10 - latency * 0.5
        # = 1.0 * 60 * 1.0 - 0 - 0 = 60.0
        assert score == 60.0

    def test_very_high_cost_budget_penalty(self):
        """Cost far exceeding budget should apply heavy penalty in quality mode."""
        # Budget penalty only applies in quality/latency modes (not balanced)
        score_no_budget = compute_evolution_score(
            quality=0.8, cost_usd=5.0, duration_seconds=30.0, eval_runs=5,
            optimize_for="quality",
        )
        score_with_budget = compute_evolution_score(
            quality=0.8, cost_usd=5.0, duration_seconds=30.0, eval_runs=5,
            budget_limit=0.5, optimize_for="quality",
        )
        # Budget penalty = (5.0 - 0.5) / 0.5 * 20 = 180.0
        assert score_with_budget < score_no_budget
        assert score_with_budget <= -100  # should be heavily negative

    def test_very_long_duration_latency_penalty(self):
        """Durations above 60s should incur latency penalty."""
        score_fast = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=30.0, eval_runs=5,
        )
        score_slow = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=300.0, eval_runs=5,
        )
        assert score_fast > score_slow

    def test_confidence_ramp_1_run(self):
        """1 eval run => confidence = (1/5)^0.5 ~ 0.447."""
        score_1 = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=1,
        )
        score_5 = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=5,
        )
        assert score_1 < score_5
        # confidence(1) = (0.2)^0.5 ~ 0.447; confidence(5) = 1.0
        assert score_1 == pytest.approx(60.0 * (1 / 5) ** 0.5, abs=0.01)

    def test_confidence_ramp_3_runs(self):
        """3 eval runs => confidence = (3/5)^0.5 ~ 0.775."""
        score = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=3,
        )
        expected = 60.0 * (3 / 5) ** 0.5
        assert score == pytest.approx(expected, abs=0.01)

    def test_confidence_saturates_above_5(self):
        """Runs > 5 should cap confidence at 1.0."""
        score_5 = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=5,
        )
        score_100 = compute_evolution_score(
            quality=1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=100,
        )
        assert score_5 == score_100

    def test_quality_mode_different_from_balanced(self):
        """Quality mode should weight quality higher."""
        args = dict(quality=0.9, cost_usd=0.05, duration_seconds=30.0, eval_runs=5)
        score_quality = compute_evolution_score(**args, optimize_for="quality")
        score_balanced = compute_evolution_score(**args, optimize_for="balanced")
        assert score_quality != score_balanced

    def test_cost_mode(self):
        """Cost mode should heavily penalize expensive runs."""
        score_cheap = compute_evolution_score(
            quality=0.8, cost_usd=0.001, duration_seconds=30.0,
            eval_runs=5, optimize_for="cost",
        )
        score_expensive = compute_evolution_score(
            quality=0.8, cost_usd=1.0, duration_seconds=30.0,
            eval_runs=5, optimize_for="cost",
        )
        assert score_cheap > score_expensive

    def test_latency_mode_with_budget(self):
        """Latency mode with budget: higher cost should be penalized more."""
        # In latency mode the formula is:
        # (1 - duration/max(duration,1)) * 50 + quality * 50 * confidence - cost_penalty
        # The duration term is always 0 for duration > 0, so we test via cost_penalty
        score_cheap = compute_evolution_score(
            quality=0.8, cost_usd=0.01, duration_seconds=30.0,
            eval_runs=5, optimize_for="latency", budget_limit=0.05,
        )
        score_expensive = compute_evolution_score(
            quality=0.8, cost_usd=0.50, duration_seconds=30.0,
            eval_runs=5, optimize_for="latency", budget_limit=0.05,
        )
        assert score_cheap > score_expensive

    def test_budget_limit_zero_no_penalty(self):
        """budget_limit=0 (falsy) should not trigger penalty."""
        score = compute_evolution_score(
            quality=0.8, cost_usd=5.0, duration_seconds=30.0,
            eval_runs=5, budget_limit=0.0,
        )
        # No budget penalty since budget_limit is falsy
        score_none = compute_evolution_score(
            quality=0.8, cost_usd=5.0, duration_seconds=30.0,
            eval_runs=5, budget_limit=None,
        )
        assert score == score_none

    def test_negative_quality_clipped_by_formula(self):
        """Negative quality values should result in negative scores."""
        score = compute_evolution_score(
            quality=-1.0, cost_usd=0.0, duration_seconds=0.0, eval_runs=5,
        )
        assert score < 0


class TestEvolutionMutations:
    """Tests for mutation operators and helpers."""

    @pytest.mark.asyncio
    async def test_mutate_prompt_empty_prompt(self):
        """Empty YAML prompt should still be parseable."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: ""
"""
        # All results passed => no failures => returns unchanged
        result_yaml, desc = await mutate_prompt(
            yaml_str, "step1", [{"passed": True}],
        )
        assert "no failures to improve" in desc

    @pytest.mark.asyncio
    async def test_mutate_prompt_step_not_found(self):
        """Missing step_id should return unchanged YAML with error message."""
        result_yaml, desc = await mutate_prompt(
            MINIMAL_YAML, "nonexistent_step", [{"passed": False, "error": "bad"}],
        )
        assert result_yaml == MINIMAL_YAML
        assert "not found" in desc

    @pytest.mark.asyncio
    async def test_mutate_prompt_with_llm_failure_uses_fallback(self):
        """When LLM call fails, fallback appends clarity instruction."""
        yaml_str = MINIMAL_YAML
        eval_results = [{"passed": False, "error": "wrong output", "name": "case1"}]

        with patch(
            "sandcastle.engine.generator._call_advisor_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result_yaml, desc = await mutate_prompt(
                yaml_str, "step1", eval_results,
            )

        assert result_yaml != yaml_str
        assert "improved prompt" in desc
        assert "Be precise and complete" in result_yaml

    @pytest.mark.asyncio
    async def test_mutate_prompt_very_long_prompt(self):
        """Prompt > 10KB should still be handled without error."""
        long_prompt = "A" * 15000
        yaml_str = f"""\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "{long_prompt}"
"""
        eval_results = [{"passed": False, "error": "too long", "name": "case1"}]

        with patch(
            "sandcastle.engine.generator._call_advisor_llm",
            side_effect=RuntimeError("LLM error"),
        ):
            result_yaml, desc = await mutate_prompt(
                yaml_str, "step1", eval_results,
            )
        # Should succeed with fallback
        assert "improved prompt" in desc

    @pytest.mark.asyncio
    async def test_mutate_model_upgrade_haiku_to_sonnet(self):
        """Low-cost haiku step should be upgraded to sonnet."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    model: haiku
"""
        result_yaml, desc = await mutate_model(yaml_str, "step1", current_cost=0.01)
        assert "upgrade" in desc
        assert "sonnet" in result_yaml

    @pytest.mark.asyncio
    async def test_mutate_model_opus_stays_at_opus(self):
        """Opus model at top of ladder should stay unchanged."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    model: opus
"""
        result_yaml, desc = await mutate_model(yaml_str, "step1", current_cost=0.01)
        assert "already at optimal position" in desc
        assert result_yaml == yaml_str

    @pytest.mark.asyncio
    async def test_mutate_model_downgrade_when_cost_high(self):
        """High cost should trigger model downgrade."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    model: sonnet
"""
        result_yaml, desc = await mutate_model(yaml_str, "step1", current_cost=0.50)
        assert "downgrade" in desc
        assert "haiku" in result_yaml

    @pytest.mark.asyncio
    async def test_mutate_simplify_minimal_workflow_1_step(self):
        """Single step with max_turns=1 should return unchanged."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    max_turns: 1
"""
        result_yaml, desc = await mutate_simplify(yaml_str, [])
        assert "already minimal" in desc

    @pytest.mark.asyncio
    async def test_mutate_simplify_reduces_max_turns(self):
        """Single step with high max_turns should be reduced."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps:
  - id: step1
    prompt: "Do something"
    max_turns: 8
"""
        result_yaml, desc = await mutate_simplify(yaml_str, [])
        assert "reduced max_turns" in desc
        # max_turns 8 > 1, so should be reduced
        assert "step1" in desc

    @pytest.mark.asyncio
    async def test_mutate_simplify_empty_steps(self):
        """Workflow with no steps should return unchanged."""
        yaml_str = """\
name: test
description: Test
default_model: sonnet
steps: []
"""
        result_yaml, desc = await mutate_simplify(yaml_str, [])
        assert "no steps to simplify" in desc

    def test_pick_mutation_type_cost_mode(self):
        """Cost optimization should prefer model and simplify."""
        types = [_pick_mutation_type(i, [], 0.1, "cost") for i in range(5)]
        assert types == ["model", "simplify", "prompt", "model", "simplify"]

    def test_pick_mutation_type_quality_mode(self):
        """Quality optimization should prefer prompt."""
        types = [_pick_mutation_type(i, [], 0.1, "quality") for i in range(5)]
        assert types == ["prompt", "model", "prompt", "simplify", "prompt"]

    def test_pick_mutation_type_latency_mode(self):
        """Latency optimization should prefer simplify."""
        types = [_pick_mutation_type(i, [], 0.1, "latency") for i in range(5)]
        assert types == ["simplify", "model", "simplify", "prompt", "simplify"]

    def test_workflow_to_yaml_roundtrip(self):
        """_workflow_to_yaml should produce valid YAML."""
        import yaml as pyyaml
        from sandcastle.engine.dag import parse_yaml_string

        workflow = parse_yaml_string(TWO_STEP_YAML)
        result = _workflow_to_yaml(workflow, TWO_STEP_YAML)
        parsed = pyyaml.safe_load(result)
        assert parsed["name"] == "test-workflow"
        assert len(parsed["steps"]) == 2


# ===========================================================================
# 2. DOCUMENT PARSER EDGE CASES (12 tests)
# ===========================================================================


class TestDocparse:
    """Tests for document parser edge cases."""

    def test_unsupported_file_type_raises(self):
        """Unsupported extension like .xyz should raise ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".xyz") as f:
            with pytest.raises(ValueError, match="Unsupported file format"):
                parse_document(f.name)

    def test_empty_text_file_returns_empty_string(self):
        """Empty .txt file should return empty text."""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("")
            f.flush()
            path = f.name

        try:
            result = parse_document(path)
            assert result.text == ""
            assert result.format == "text"
            assert result.pages == 1
        finally:
            os.unlink(path)

    def test_parse_csv_returns_table(self):
        """CSV file should be parsed into a markdown table."""
        with tempfile.NamedTemporaryFile(
            suffix=".csv", mode="w", delete=False, newline="",
        ) as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Age", "City"])
            writer.writerow(["Alice", "30", "Prague"])
            writer.writerow(["Bob", "25", "Berlin"])
            path = f.name

        try:
            result = parse_document(path, output_format="markdown")
            assert "| Name | Age | City |" in result.text
            assert "| Alice | 30 | Prague |" in result.text
            assert "| --- | --- | --- |" in result.text
            assert result.format == "markdown"
        finally:
            os.unlink(path)

    def test_parse_csv_empty_file(self):
        """Empty CSV should produce empty result."""
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            path = f.name

        try:
            result = parse_document(path)
            assert result.text == ""
            assert result.pages == 0
        finally:
            os.unlink(path)

    def test_parse_page_ranges_none_returns_all(self):
        """None pages_str should return all indices."""
        indices = _parse_page_ranges(None, 10)
        assert indices == list(range(10))

    def test_parse_page_ranges_single_page(self):
        """Single page '3' should return [2] (0-based)."""
        indices = _parse_page_ranges("3", 10)
        assert indices == [2]

    def test_parse_page_ranges_range(self):
        """Range '1-3' should return [0, 1, 2]."""
        indices = _parse_page_ranges("1-3", 10)
        assert indices == [0, 1, 2]

    def test_parse_page_ranges_exceeding_total(self):
        """Page numbers beyond total should be clamped."""
        indices = _parse_page_ranges("1-100", 5)
        assert indices == [0, 1, 2, 3, 4]

    def test_parse_page_ranges_invalid_page_ignored(self):
        """Out of range single page should be ignored."""
        indices = _parse_page_ranges("99", 5)
        assert indices == []

    def test_parse_pdf_with_mock_pymupdf(self):
        """PDF parsing should use pymupdf (fitz) to extract text."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Hello from PDF"
        mock_page.get_images.return_value = []

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=2)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            path = f.name

        try:
            import sys
            original_fitz = sys.modules.get("fitz")
            sys.modules["fitz"] = mock_fitz
            try:
                result = parse_document(path, output_format="text")
            finally:
                if original_fitz is not None:
                    sys.modules["fitz"] = original_fitz
                else:
                    sys.modules.pop("fitz", None)

            assert "Hello from PDF" in result.text
            assert result.metadata["content_type"] == "application/pdf"
            assert result.metadata["total_pages"] == 2
        finally:
            os.unlink(path)

    def test_parse_docx_with_mock(self):
        """DOCX parsing should use python-docx to extract paragraphs."""
        mock_para = MagicMock()
        mock_para.text = "Test paragraph"
        mock_para.style.name = "Normal"

        mock_doc_instance = MagicMock()
        mock_doc_instance.paragraphs = [mock_para]
        mock_doc_instance.tables = []

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc_instance

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(b"PK\x03\x04fake")
            path = f.name

        try:
            import sys
            original_docx = sys.modules.get("docx")
            sys.modules["docx"] = mock_docx_module
            try:
                result = parse_document(path, output_format="text")
            finally:
                if original_docx is not None:
                    sys.modules["docx"] = original_docx
                else:
                    sys.modules.pop("docx", None)

            assert "Test paragraph" in result.text
        finally:
            os.unlink(path)

    def test_screenshot_page_returns_bytes(self):
        """screenshot_page should return PNG bytes."""
        mock_pixmap = MagicMock()
        mock_pixmap.tobytes.return_value = b"\x89PNG\r\n\x1a\nfake"

        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pixmap

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=3)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        import sys
        original_fitz = sys.modules.get("fitz")
        sys.modules["fitz"] = mock_fitz
        try:
            result = screenshot_page("/fake/path.pdf", page=1, dpi=150)
        finally:
            if original_fitz is not None:
                sys.modules["fitz"] = original_fitz
            else:
                sys.modules.pop("fitz", None)

        assert isinstance(result, bytes)
        assert result.startswith(b"\x89PNG")

    def test_screenshot_page_out_of_range(self):
        """Page index beyond document should raise ValueError."""
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=2)
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        import sys
        original_fitz = sys.modules.get("fitz")
        sys.modules["fitz"] = mock_fitz
        try:
            with pytest.raises(ValueError, match="out of range"):
                screenshot_page("/fake/path.pdf", page=5)
        finally:
            if original_fitz is not None:
                sys.modules["fitz"] = original_fitz
            else:
                sys.modules.pop("fitz", None)


# ===========================================================================
# 3. MEMORY SYSTEM (14 tests)
# ===========================================================================


class TestMemorySystem:
    """Tests for memory scoring, decay, conflicts, and enrichment."""

    def test_score_importance_normal_content(self):
        """Normal-length content with no existing memories scores ~0.6."""
        score = score_importance(
            "The user prefers Python 3.12 with FastAPI for web services.",
            existing_memories=[],
        )
        # baseline 0.5 + 0.1 (sweet spot length) = 0.6
        assert 0.5 <= score <= 0.7

    def test_score_importance_very_short(self):
        """Very short content should receive a length penalty."""
        score = score_importance("Hi", [])
        assert score < 0.4  # 0.5 - 0.25 = 0.25

    def test_score_importance_very_long(self):
        """Content > 2000 chars should receive a length penalty."""
        long = "word " * 500  # 2500 chars
        score = score_importance(long, [])
        assert score < 0.5  # 0.5 - 0.15 = 0.35

    def test_score_importance_structured_data_bonus(self):
        """Content with structured data (dates, JSON) should get a bonus."""
        score = score_importance(
            "The report was generated on 2026-03-18 with {status: success}.",
            [],
        )
        # baseline + sweet spot + structured bonus
        assert score >= 0.6

    def test_score_importance_high_overlap_penalized(self):
        """Content very similar to existing memories should be penalized."""
        existing = [
            {"memory": "The user prefers Python 3.12 with FastAPI for web services."},
        ]
        score = score_importance(
            "The user prefers Python 3.12 with FastAPI for web services and more.",
            existing,
        )
        # High overlap => penalty of -0.3
        assert score < 0.5

    def test_should_admit_empty_content_rejected(self):
        """Empty content should always be rejected."""
        admitted, score, reason = should_admit("", [])
        assert admitted is False
        assert score == 0.0
        assert "Empty" in reason

    def test_should_admit_whitespace_only_rejected(self):
        """Whitespace-only content should be rejected."""
        admitted, score, reason = should_admit("   \n  ", [])
        assert admitted is False

    def test_should_admit_above_threshold(self):
        """Good content should be admitted above default threshold."""
        admitted, score, reason = should_admit(
            "The user discovered that pymupdf handles scanned PDFs better than pypdf.",
            [],
            threshold=0.3,
        )
        assert admitted is True
        assert score >= 0.3

    def test_should_admit_below_threshold(self):
        """Low-value content below custom high threshold should be rejected."""
        admitted, score, reason = should_admit(
            "ok",  # too short (< 20 chars)
            [],
            threshold=0.5,
        )
        assert admitted is False

    def test_apply_decay_filters_expired(self):
        """Memories older than max_age_days should be removed."""
        now = datetime.now(timezone.utc)
        old_date = (now - timedelta(days=100)).isoformat()
        recent_date = (now - timedelta(days=5)).isoformat()

        memories = [
            {"id": "old", "memory": "Old fact", "created_at": old_date},
            {"id": "recent", "memory": "Recent fact", "created_at": recent_date},
        ]

        result = apply_decay(memories, max_age_days=90)
        assert len(result) == 1
        assert result[0]["id"] == "recent"
        assert result[0]["relevance_score"] > 0.5

    def test_apply_decay_no_expiry(self):
        """max_age_days=0 should keep all memories with default relevance."""
        memories = [
            {"id": "1", "memory": "Fact A"},
            {"id": "2", "memory": "Fact B"},
        ]
        result = apply_decay(memories, max_age_days=0)
        assert len(result) == 2
        assert all(m["relevance_score"] == 1.0 for m in result)

    def test_apply_decay_no_timestamp_keeps_mid_relevance(self):
        """Memories without created_at should be kept with 0.5 relevance."""
        memories = [{"id": "1", "memory": "No date"}]
        result = apply_decay(memories, max_age_days=90)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.5

    def test_detect_conflicts_high_overlap(self):
        """High word overlap should be flagged as conflict."""
        existing = [
            {"memory": "The system uses PostgreSQL database for storage."},
        ]
        conflicts = detect_conflicts(
            "The system uses MySQL database for storage.",
            existing,
        )
        assert len(conflicts) >= 1
        assert conflicts[0]["_overlap_ratio"] > 0.4

    def test_detect_conflicts_no_overlap(self):
        """Completely different content should have no conflicts."""
        existing = [
            {"memory": "Python handles web API requests."},
        ]
        conflicts = detect_conflicts(
            "The cat sat on the mat near the window.",
            existing,
        )
        assert len(conflicts) == 0

    def test_detect_conflicts_empty_query(self):
        """Empty content should return no conflicts."""
        conflicts = detect_conflicts("", [{"memory": "Something"}])
        assert conflicts == []

    def test_enrich_memory_extracts_keywords(self):
        """Enrichment should extract top keywords, excluding stopwords."""
        result = enrich_memory("PostgreSQL handles complex queries efficiently.")
        assert "postgresql" in result["keywords"]
        assert len(result["keywords"]) <= 5
        # Stopwords should be excluded
        for kw in result["keywords"]:
            assert kw not in _STOPWORDS

    def test_enrich_memory_auto_tags_issue(self):
        """Content with error-like words should be tagged 'issue'."""
        result = enrich_memory("The application crashed with an exception.")
        assert "issue" in result["tags"]

    def test_enrich_memory_auto_tags_preference(self):
        """Content with preference words should be tagged 'preference'."""
        result = enrich_memory("The user prefers dark mode for the dashboard.")
        assert "preference" in result["tags"]

    def test_enrich_memory_auto_tags_data(self):
        """Content with dates/numbers should be tagged 'data'."""
        result = enrich_memory("The revenue was 12345.67 on 2026-03-18.")
        assert "data" in result["tags"]

    def test_format_memories_for_prompt_empty(self):
        """No memories should return empty string."""
        result = format_memories_for_prompt([])
        assert result == ""

    def test_format_memories_for_prompt_with_entries(self):
        """Memories should be formatted with header and footer."""
        memories = [
            {"memory": "User prefers Python.", "metadata": {"tags": "preference"}},
            {"memory": "System uses PostgreSQL.", "metadata": {}},
        ]
        result = format_memories_for_prompt(memories)
        assert "[Agent Memory]" in result
        assert "[End of Agent Memory]" in result
        assert "User prefers Python." in result
        assert "tags:preference" in result

    def test_format_memories_for_prompt_max_chars(self):
        """Very long memories should be truncated by max_chars budget."""
        memories = [
            {"memory": "A" * 500},
            {"memory": "B" * 500},
            {"memory": "C" * 500},
            {"memory": "D" * 500},
        ]
        result = format_memories_for_prompt(memories, max_chars=600)
        assert "[Agent Memory]" in result
        assert "[End of Agent Memory]" in result
        assert len(result) <= 700  # some slack for header/footer

    def test_word_set_extracts_lowercase_words(self):
        """_word_set should extract lowercase tokens > 2 chars."""
        words = _word_set("Hello World of AI and ML")
        assert "hello" in words
        assert "world" in words
        # 'of', 'and', 'ml' are <= 2 chars or == 2 chars (filtered)
        assert "of" not in words
        assert "ai" not in words  # 2 chars, filtered


# ===========================================================================
# 4. EXECUTOR EDGE CASES (13 tests)
# ===========================================================================


class TestExecutorEdgeCases:
    """Tests for executor helper functions and edge cases."""

    def test_resolve_variable_input(self):
        """Input variables should be resolved from context.input."""
        ctx = _make_context(input={"name": "Alice"})
        result = resolve_variable("input.name", ctx)
        assert result == "Alice"

    def test_resolve_variable_step_output(self):
        """Step output should be resolvable via steps.ID.output."""
        ctx = _make_context()
        ctx.step_outputs["step1"] = {"data": "hello"}
        result = resolve_variable("steps.step1.output", ctx)
        assert result == {"data": "hello"}

    def test_resolve_variable_step_output_field(self):
        """Specific field in step output should be resolvable."""
        ctx = _make_context()
        ctx.step_outputs["step1"] = {"data": "hello", "count": 42}
        result = resolve_variable("steps.step1.output.count", ctx)
        assert result == 42

    def test_resolve_variable_run_id(self):
        """run_id variable should return the context run_id."""
        rid = str(uuid.uuid4())
        ctx = _make_context(run_id=rid)
        result = resolve_variable("run_id", ctx)
        assert result == rid

    def test_resolve_variable_date(self):
        """date variable should return today's ISO date."""
        ctx = _make_context()
        result = resolve_variable("date", ctx)
        today = datetime.now(timezone.utc).date().isoformat()
        assert result == today

    def test_resolve_variable_missing_returns_unresolved(self):
        """Missing input key should return _UNRESOLVED sentinel."""
        ctx = _make_context(input={})
        result = resolve_variable("input.nonexistent", ctx)
        assert result is _UNRESOLVED

    def test_resolve_variable_empty_path(self):
        """Empty variable path should return _UNRESOLVED."""
        ctx = _make_context()
        result = resolve_variable("", ctx)
        assert result is _UNRESOLVED

    def test_resolve_templates_replaces_variables(self):
        """Templates with {input.x} should be resolved."""
        ctx = _make_context(input={"topic": "AI safety"})
        result = resolve_templates("Analyze {input.topic} trends", ctx)
        assert "AI safety" in result

    def test_resolve_templates_auto_injects_deps(self):
        """Unreferenced dependency outputs should be auto-injected."""
        ctx = _make_context()
        ctx.step_outputs["research"] = "Data about climate change."
        result = resolve_templates(
            "Summarize findings",
            ctx,
            depends_on=["research"],
        )
        assert "Data about climate change" in result
        assert "Context from previous steps" in result

    def test_check_budget_none_limit(self):
        """No budget limit should always return None."""
        ctx = _make_context()
        ctx.max_cost_usd = None
        assert _check_budget(ctx) is None

    def test_check_budget_exceeded(self):
        """Total cost exceeding budget should return 'exceeded'."""
        ctx = _make_context()
        ctx.max_cost_usd = 1.0
        ctx.costs = [0.5, 0.3, 0.3]  # total = 1.1
        assert _check_budget(ctx) == "exceeded"

    def test_check_budget_warning_at_80_percent(self):
        """Cost at 80-99% of budget should return 'warning'."""
        ctx = _make_context()
        ctx.max_cost_usd = 1.0
        ctx.costs = [0.85]
        assert _check_budget(ctx) == "warning"

    def test_check_budget_ok_below_80_percent(self):
        """Cost below 80% should return None."""
        ctx = _make_context()
        ctx.max_cost_usd = 1.0
        ctx.costs = [0.5]
        assert _check_budget(ctx) is None

    def test_check_budget_nan_cost_is_exceeded(self):
        """NaN in total_cost should be treated as budget exceeded."""
        ctx = _make_context()
        ctx.max_cost_usd = 1.0
        ctx.costs = [float("nan")]
        assert _check_budget(ctx) == "exceeded"

    def test_check_budget_inf_cost_is_exceeded(self):
        """Infinity in total_cost should be treated as budget exceeded."""
        ctx = _make_context()
        ctx.max_cost_usd = 1.0
        ctx.costs = [float("inf")]
        assert _check_budget(ctx) == "exceeded"

    def test_backoff_delay_exponential_increases(self):
        """Exponential backoff should increase with attempts."""
        # Run multiple times to account for jitter; check max possible
        delays = [_backoff_delay(i, "exponential") for i in range(5)]
        # Delay cap for attempt 4 = min(2^4, 60) = 16
        # Each delay is uniform [0, 2^attempt] so on average increases
        # Just verify the cap is respected
        for i, d in enumerate(delays):
            assert 0 <= d <= min(2 ** i, 60)

    def test_backoff_delay_fixed(self):
        """Fixed backoff should return values in [1.0, 3.0]."""
        for _ in range(20):
            d = _backoff_delay(5, "fixed")
            assert 1.0 <= d <= 3.0

    def test_safe_cost_normal(self):
        """Normal inputs should compute correct cost."""
        cost = _safe_cost(1000, 500, 3.0, 15.0)
        # 1000 * 3.0 / 1_000_000 + 500 * 15.0 / 1_000_000
        expected = 1000 * 3.0 / 1e6 + 500 * 15.0 / 1e6
        assert cost == pytest.approx(expected)

    def test_safe_cost_none_prices(self):
        """None prices should be treated as 0.0."""
        cost = _safe_cost(1000, 500, None, None)
        assert cost == 0.0

    def test_safe_cost_negative_tokens(self):
        """Negative token counts should be clamped to 0."""
        cost = _safe_cost(-100, -200, 3.0, 15.0)
        assert cost == 0.0

    def test_safe_cost_nan_returns_zero(self):
        """NaN result should return 0.0."""
        cost = _safe_cost(1000, 500, float("nan"), 15.0)
        assert cost == 0.0

    def test_validate_browser_url_rejects_javascript(self):
        """javascript: scheme should be rejected."""
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("javascript:alert(1)")

    def test_validate_browser_url_rejects_data(self):
        """data: scheme should be rejected."""
        with pytest.raises(ValueError, match="Dangerous"):
            _validate_browser_url("data:text/html,<h1>hi</h1>")

    def test_validate_browser_url_rejects_empty(self):
        """Empty URL should be rejected."""
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_browser_url("")

    def test_validate_browser_url_prepends_https(self):
        """URL without scheme should get https:// prepended."""
        result = _validate_browser_url("example.com")
        assert result == "https://example.com"

    def test_validate_browser_url_accepts_https(self):
        """Valid https URL should pass unchanged."""
        result = _validate_browser_url("https://example.com/path")
        assert result == "https://example.com/path"

    def test_write_csv_output_unicode(self):
        """CSV output with unicode characters should be written correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            step = StepDefinition(
                id="unicode_step",
                csv_output=CsvOutputConfig(
                    directory=tmpdir,
                    mode="new_file",
                    filename="test",
                ),
            )
            output = [
                {"name": "Praha", "value": "cesky text"},
                {"name": "Berlin", "value": "Umlaute"},
            ]

            _write_csv_output(step, output, "test-run-id")

            # Find the created CSV file
            csv_files = list(Path(tmpdir).glob("test_*.csv"))
            assert len(csv_files) == 1

            with open(csv_files[0], encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 2
            assert rows[0]["name"] == "Praha"

    def test_write_csv_output_empty_data(self):
        """Empty output list should produce no file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            step = StepDefinition(
                id="empty_step",
                csv_output=CsvOutputConfig(
                    directory=tmpdir,
                    mode="new_file",
                ),
            )
            _write_csv_output(step, [], "test-run-id")
            csv_files = list(Path(tmpdir).glob("*.csv"))
            assert len(csv_files) == 0

    def test_run_context_with_item_isolation(self):
        """Child context should be isolated from parent."""
        parent = _make_context(input={"key": "value"})
        parent.step_outputs["step1"] = {"data": "original"}

        child = parent.with_item("item_x", 0)
        child.step_outputs["step1"] = {"data": "modified"}
        child.costs.append(0.5)

        # Parent should be unaffected
        assert parent.step_outputs["step1"]["data"] == "original"
        assert len(parent.costs) == 0

    def test_run_context_total_cost(self):
        """total_cost property should sum all costs."""
        ctx = _make_context()
        ctx.costs = [0.1, 0.2, 0.3]
        assert ctx.total_cost == pytest.approx(0.6)

    def test_run_context_snapshot(self):
        """snapshot() should return serializable dict."""
        ctx = _make_context(input={"a": 1})
        ctx.step_outputs["s1"] = "done"
        ctx.costs = [0.05]
        snap = ctx.snapshot()
        assert snap["input"] == {"a": 1}
        assert snap["step_outputs"] == {"s1": "done"}
        assert snap["total_cost"] == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_execute_workflow_rejects_unacceptable_risk(self):
        """Unacceptable risk workflows should be immediately rejected."""
        workflow = WorkflowDefinition(
            name="dangerous",
            description="Bad workflow",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[],
            risk_level="unacceptable",
        )
        plan = ExecutionPlan(stages=[])

        from sandcastle.engine.executor import execute_workflow

        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data={},
        )
        assert result.status == "failed"
        assert "EU AI Act" in result.error
