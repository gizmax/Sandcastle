"""Tests for the workflow executor."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.dag import (
    CsvOutputConfig,
    RetryConfig,
    StepDefinition,
    build_plan,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    _write_csv_output,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    resolve_variable,
)
from sandcastle.engine.sandbox import SandstormClient, SandstormResult

# --- Fixtures ---


def make_context(**kwargs) -> RunContext:
    """Create a test RunContext."""
    return RunContext(
        run_id=kwargs.get("run_id", "test-run-123"),
        input=kwargs.get("input", {}),
        step_outputs=kwargs.get("step_outputs", {}),
    )


def make_step(**kwargs) -> StepDefinition:
    """Create a test StepDefinition."""
    return StepDefinition(
        id=kwargs.get("id", "test-step"),
        prompt=kwargs.get("prompt", "Test prompt"),
        model=kwargs.get("model", "sonnet"),
        max_turns=kwargs.get("max_turns", 5),
        timeout=kwargs.get("timeout", 60),
        retry=kwargs.get("retry"),
        output_schema=kwargs.get("output_schema"),
    )


# --- Tests: resolve_variable ---


class TestResolveVariable:
    def test_input_simple(self):
        ctx = make_context(input={"name": "Acme Corp"})
        assert resolve_variable("input.name", ctx) == "Acme Corp"

    def test_input_nested(self):
        ctx = make_context(input={"company": {"name": "Acme", "size": 50}})
        assert resolve_variable("input.company.name", ctx) == "Acme"
        assert resolve_variable("input.company.size", ctx) == 50

    def test_input_list(self):
        ctx = make_context(input={"items": ["a", "b", "c"]})
        assert resolve_variable("input.items.1", ctx) == "b"

    def test_step_output(self):
        ctx = make_context(step_outputs={"scrape": {"title": "Hello"}})
        assert resolve_variable("steps.scrape.output", ctx) == {"title": "Hello"}

    def test_step_output_field(self):
        ctx = make_context(step_outputs={"scrape": {"title": "Hello", "score": 42}})
        assert resolve_variable("steps.scrape.output.title", ctx) == "Hello"
        assert resolve_variable("steps.scrape.output.score", ctx) == 42

    def test_run_id(self):
        ctx = make_context(run_id="abc-123")
        assert resolve_variable("run_id", ctx) == "abc-123"

    def test_date(self):
        ctx = make_context()
        result = resolve_variable("date", ctx)
        assert isinstance(result, str)
        assert len(result) == 10  # YYYY-MM-DD

    def test_missing_input(self):
        ctx = make_context(input={})
        assert resolve_variable("input.nonexistent", ctx) is None

    def test_missing_step(self):
        ctx = make_context()
        assert resolve_variable("steps.missing.output", ctx) is None

    def test_unknown_variable(self):
        ctx = make_context()
        assert resolve_variable("unknown.path", ctx) is None


# --- Tests: resolve_templates ---


class TestResolveTemplates:
    def test_simple_replacement(self):
        ctx = make_context(input={"name": "Acme"})
        result = resolve_templates("Hello {input.name}!", ctx)
        assert result == "Hello Acme!"

    def test_multiple_replacements(self):
        ctx = make_context(
            input={"name": "Acme"},
            step_outputs={"scrape": "scraped data"},
        )
        result = resolve_templates(
            "Company: {input.name}, Data: {steps.scrape.output}", ctx
        )
        assert result == "Company: Acme, Data: scraped data"

    def test_dict_replacement(self):
        ctx = make_context(step_outputs={"step1": {"key": "value"}})
        result = resolve_templates("Result: {steps.step1.output}", ctx)
        assert result == 'Result: {"key": "value"}'

    def test_unresolved_stays(self):
        ctx = make_context()
        result = resolve_templates("Keep {unknown.var} as is", ctx)
        assert result == "Keep {unknown.var} as is"

    def test_run_id_in_template(self):
        ctx = make_context(run_id="my-run")
        result = resolve_templates("Run: {run_id}", ctx)
        assert result == "Run: my-run"


# --- Tests: execute_step_with_retry ---


class TestExecuteStepWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        step = make_step()
        ctx = make_context()

        mock_sandbox = AsyncMock(spec=SandstormClient)
        mock_sandbox.query.return_value = SandstormResult(
            text="result text",
            structured_output={"answer": 42},
            total_cost_usd=0.01,
        )

        mock_storage = AsyncMock()
        mock_storage.read.return_value = None

        result = await execute_step_with_retry(step, ctx, mock_sandbox, mock_storage)

        assert result.status == "completed"
        assert result.output == {"answer": 42}
        assert result.cost_usd == 0.01
        assert result.attempt == 1
        mock_sandbox.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        step = make_step(retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"))
        ctx = make_context()

        mock_sandbox = AsyncMock(spec=SandstormClient)
        # Fail twice, succeed on third
        mock_sandbox.query.side_effect = [
            Exception("fail 1"),
            Exception("fail 2"),
            SandstormResult(text="ok", total_cost_usd=0.01),
        ]

        mock_storage = AsyncMock()
        mock_storage.read.return_value = None

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(step, ctx, mock_sandbox, mock_storage)

        assert result.status == "completed"
        assert result.attempt == 3
        assert mock_sandbox.query.call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        step = make_step(retry=RetryConfig(max_attempts=2, backoff="fixed", on_failure="abort"))
        ctx = make_context()

        mock_sandbox = AsyncMock(spec=SandstormClient)
        mock_sandbox.query.side_effect = Exception("always fails")

        mock_storage = AsyncMock()
        mock_storage.read.return_value = None

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(step, ctx, mock_sandbox, mock_storage)

        assert result.status == "failed"
        assert "always fails" in result.error
        assert result.attempt == 2

    @pytest.mark.asyncio
    async def test_no_retry_config(self):
        step = make_step(retry=None)
        ctx = make_context()

        mock_sandbox = AsyncMock(spec=SandstormClient)
        mock_sandbox.query.side_effect = Exception("single failure")

        mock_storage = AsyncMock()
        mock_storage.read.return_value = None

        result = await execute_step_with_retry(step, ctx, mock_sandbox, mock_storage)

        assert result.status == "failed"
        assert result.attempt == 1
        mock_sandbox.query.assert_called_once()


# --- Tests: execute_workflow ---


class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_simple_workflow(self):
        yaml_content = """
name: simple
description: test
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Hello"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        mock_result = SandstormResult(
            text="Hello response",
            total_cost_usd=0.005,
        )

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch("sandcastle.engine.storage.LocalStorage") as MockStorage,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.return_value = mock_result
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            mock_storage = AsyncMock()
            mock_storage.read.return_value = None
            MockStorage.return_value = mock_storage

            result = await execute_workflow(workflow, plan, input_data={})

        assert result.status == "completed"
        assert result.outputs["step1"] == "Hello response"
        assert result.total_cost_usd == 0.005

    @pytest.mark.asyncio
    async def test_workflow_with_dependency(self):
        yaml_content = """
name: chain
description: test chain
sandstorm_url: http://localhost:8000
steps:
  - id: first
    prompt: "First step"
  - id: second
    depends_on: [first]
    prompt: "Second using {steps.first.output}"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        results = [
            SandstormResult(text="first result", total_cost_usd=0.01),
            SandstormResult(text="second result", total_cost_usd=0.02),
        ]

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch("sandcastle.engine.storage.LocalStorage") as MockStorage,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.side_effect = results
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            mock_storage = AsyncMock()
            mock_storage.read.return_value = None
            MockStorage.return_value = mock_storage

            result = await execute_workflow(workflow, plan, input_data={})

        assert result.status == "completed"
        assert result.outputs["first"] == "first result"
        assert result.outputs["second"] == "second result"
        assert result.total_cost_usd == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_workflow_step_failure_abort(self):
        yaml_content = """
name: fail-test
description: test failure
sandstorm_url: http://localhost:8000
steps:
  - id: failing
    prompt: "This will fail"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)

        with (
            patch("sandcastle.engine.executor.SandstormClient") as MockClient,
            patch("sandcastle.engine.storage.LocalStorage") as MockStorage,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.query.side_effect = Exception("Sandstorm error")
            mock_sandbox.close = AsyncMock()
            MockClient.return_value = mock_sandbox

            mock_storage = AsyncMock()
            mock_storage.read.return_value = None
            MockStorage.return_value = mock_storage

            result = await execute_workflow(workflow, plan, input_data={})

        assert result.status == "failed"
        assert result.error is not None


# --- Tests: _write_csv_output ---


class TestWriteCsvOutput:
    def test_dict_output_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = make_step(id="export")
            step.csv_output = CsvOutputConfig(
                directory=tmpdir, mode="new_file", filename="test"
            )
            output = {"name": "Alice", "score": 95}
            _write_csv_output(step, output, "run-123")

            files = list(Path(tmpdir).glob("test_*.csv"))
            assert len(files) == 1
            with open(files[0]) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["name"] == "Alice"
            assert rows[0]["score"] == "95"

    def test_list_of_dicts_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = make_step(id="export")
            step.csv_output = CsvOutputConfig(
                directory=tmpdir, mode="new_file", filename="multi"
            )
            output = [
                {"name": "Alice", "score": 95},
                {"name": "Bob", "score": 87},
            ]
            _write_csv_output(step, output, "run-456")

            files = list(Path(tmpdir).glob("multi_*.csv"))
            assert len(files) == 1
            with open(files[0]) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 2
            assert rows[1]["name"] == "Bob"

    def test_append_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = make_step(id="log")
            step.csv_output = CsvOutputConfig(
                directory=tmpdir, mode="append", filename="log"
            )
            # First write
            _write_csv_output(step, {"event": "start"}, "run-1")
            # Second write
            _write_csv_output(step, {"event": "end"}, "run-2")

            filepath = Path(tmpdir) / "log.csv"
            assert filepath.exists()
            with open(filepath) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 2
            assert rows[0]["event"] == "start"
            assert rows[1]["event"] == "end"

    def test_string_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = make_step(id="text")
            step.csv_output = CsvOutputConfig(
                directory=tmpdir, mode="new_file", filename="text"
            )
            _write_csv_output(step, "Hello world", "run-str")

            files = list(Path(tmpdir).glob("text_*.csv"))
            assert len(files) == 1
            with open(files[0]) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["value"] == "Hello world"

    def test_default_filename_uses_step_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = make_step(id="my-step")
            step.csv_output = CsvOutputConfig(
                directory=tmpdir, mode="new_file", filename=""
            )
            _write_csv_output(step, {"x": 1}, "run-id")

            files = list(Path(tmpdir).glob("my-step_*.csv"))
            assert len(files) == 1

    def test_no_csv_output_config(self):
        """Should silently do nothing when csv_output is None."""
        step = make_step(id="noop")
        step.csv_output = None
        # Should not raise
        _write_csv_output(step, {"x": 1}, "run-noop")

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = str(Path(tmpdir) / "a" / "b" / "c")
            step = make_step(id="deep")
            step.csv_output = CsvOutputConfig(
                directory=nested, mode="new_file", filename="deep"
            )
            _write_csv_output(step, {"val": 42}, "run-dir")

            files = list(Path(nested).glob("deep_*.csv"))
            assert len(files) == 1
