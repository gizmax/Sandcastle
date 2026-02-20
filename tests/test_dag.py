"""Tests for the DAG parser and dependency resolver."""

from __future__ import annotations

import os
import tempfile

import pytest

from sandcastle.engine.dag import (
    build_plan,
    parse,
    parse_yaml_string,
    validate,
)

# --- Fixtures ---


SIMPLE_WORKFLOW_YAML = """
name: test-workflow
description: A simple test workflow

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: step1
    prompt: "Do thing 1"
  - id: step2
    depends_on: [step1]
    prompt: "Do thing 2 with {steps.step1.output}"
  - id: step3
    depends_on: [step1]
    prompt: "Do thing 3"
  - id: step4
    depends_on: [step2, step3]
    prompt: "Combine results"
"""

PARALLEL_WORKFLOW_YAML = """
name: parallel-test
description: Workflow with parallel steps

default_model: sonnet
default_max_turns: 5
default_timeout: 120

steps:
  - id: a
    prompt: "Task A"
  - id: b
    prompt: "Task B"
  - id: c
    depends_on: [a, b]
    prompt: "Combine A and B"
"""


CYCLE_WORKFLOW_YAML = """
name: cycle-test
description: Workflow with a cycle

default_model: sonnet

steps:
  - id: a
    depends_on: [c]
    prompt: "A depends on C"
  - id: b
    depends_on: [a]
    prompt: "B depends on A"
  - id: c
    depends_on: [b]
    prompt: "C depends on B"
"""


FULL_WORKFLOW_YAML = """
name: lead-enrichment
description: Enrich companies

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: scrape
    prompt: "Scrape {input.website}"
    model: sonnet
    max_turns: 10
    timeout: 120
    parallel_over: input.companies
    output_schema:
      type: object
      properties:
        description: { type: string }
    retry:
      max_attempts: 3
      backoff: exponential
      on_failure: skip

  - id: enrich
    depends_on: [scrape]
    prompt: "Enrich {steps.scrape.output}"
    model: sonnet
    max_turns: 15

  - id: score
    depends_on: [scrape, enrich]
    prompt: "Score lead"
    model: haiku
    max_turns: 5

on_complete:
  storage_path: results/{run_id}/output.json

on_failure:
  dead_letter: true
"""


# --- Tests: parse ---


class TestParse:
    def test_parse_from_string(self):
        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        assert workflow.name == "test-workflow"
        assert len(workflow.steps) == 4
        assert workflow.default_model == "sonnet"

    def test_parse_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(SIMPLE_WORKFLOW_YAML)
            f.flush()
            workflow = parse(f.name)
        os.unlink(f.name)
        assert workflow.name == "test-workflow"
        assert len(workflow.steps) == 4

    def test_parse_step_defaults(self):
        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        step1 = workflow.get_step("step1")
        assert step1.model == "sonnet"
        assert step1.max_turns == 10
        assert step1.timeout == 300

    def test_parse_step_overrides(self):
        workflow = parse_yaml_string(FULL_WORKFLOW_YAML)
        scrape = workflow.get_step("scrape")
        assert scrape.timeout == 120
        assert scrape.parallel_over == "input.companies"
        assert scrape.output_schema is not None
        assert scrape.retry is not None
        assert scrape.retry.max_attempts == 3
        assert scrape.retry.on_failure == "skip"

    def test_parse_depends_on(self):
        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        step4 = workflow.get_step("step4")
        assert step4.depends_on == ["step2", "step3"]

    def test_parse_on_complete(self):
        workflow = parse_yaml_string(FULL_WORKFLOW_YAML)
        assert workflow.on_complete is not None
        assert workflow.on_complete.storage_path == "results/{run_id}/output.json"

    def test_parse_on_failure(self):
        workflow = parse_yaml_string(FULL_WORKFLOW_YAML)
        assert workflow.on_failure is not None
        assert workflow.on_failure.dead_letter is True

    def test_parse_env_var_interpolation(self):
        os.environ["TEST_WEBHOOK_URL"] = "http://custom:9000/hook"
        yaml_content = """
name: env-test
description: test
on_complete:
  webhook: ${TEST_WEBHOOK_URL}
steps:
  - id: step1
    prompt: "hello"
"""
        workflow = parse_yaml_string(yaml_content)
        assert workflow.on_complete.webhook == "http://custom:9000/hook"
        del os.environ["TEST_WEBHOOK_URL"]

    def test_get_step_not_found(self):
        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        with pytest.raises(ValueError, match="not found"):
            workflow.get_step("nonexistent")


# --- Tests: validate ---


class TestValidate:
    def test_valid_workflow(self):
        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        errors = validate(workflow)
        assert errors == []

    def test_empty_steps(self):
        yaml_content = """
name: empty
description: no steps
steps: []
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("at least one step" in e for e in errors)

    def test_duplicate_step_ids(self):
        yaml_content = """
name: duplicates
description: duplicate IDs
steps:
  - id: step1
    prompt: "first"
  - id: step1
    prompt: "duplicate"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("Duplicate" in e for e in errors)

    def test_unknown_dependency(self):
        yaml_content = """
name: bad-dep
description: unknown dep
steps:
  - id: step1
    depends_on: [nonexistent]
    prompt: "bad dep"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("unknown step" in e for e in errors)

    def test_cycle_detection(self):
        workflow = parse_yaml_string(CYCLE_WORKFLOW_YAML)
        errors = validate(workflow)
        assert any("Cycle" in e for e in errors)


# --- Tests: build_plan ---


class TestBuildPlan:
    def test_simple_linear(self):
        yaml_content = """
name: linear
description: linear workflow
steps:
  - id: a
    prompt: "A"
  - id: b
    depends_on: [a]
    prompt: "B"
  - id: c
    depends_on: [b]
    prompt: "C"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)
        assert plan.stages == [["a"], ["b"], ["c"]]

    def test_parallel_stages(self):
        workflow = parse_yaml_string(PARALLEL_WORKFLOW_YAML)
        plan = build_plan(workflow)
        # a and b have no deps → same stage; c depends on both → next stage
        assert plan.stages[0] == ["a", "b"]
        assert plan.stages[1] == ["c"]

    def test_diamond_dependency(self):
        workflow = parse_yaml_string(SIMPLE_WORKFLOW_YAML)
        plan = build_plan(workflow)
        # step1 → [step2, step3] → step4
        assert plan.stages[0] == ["step1"]
        assert sorted(plan.stages[1]) == ["step2", "step3"]
        assert plan.stages[2] == ["step4"]

    def test_cycle_raises(self):
        workflow = parse_yaml_string(CYCLE_WORKFLOW_YAML)
        with pytest.raises(ValueError, match="unschedulable"):
            build_plan(workflow)

    def test_single_step(self):
        yaml_content = """
name: single
description: single step
steps:
  - id: only
    prompt: "The only step"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)
        assert plan.stages == [["only"]]

    def test_all_independent(self):
        yaml_content = """
name: independent
description: all independent
steps:
  - id: a
    prompt: "A"
  - id: b
    prompt: "B"
  - id: c
    prompt: "C"
"""
        workflow = parse_yaml_string(yaml_content)
        plan = build_plan(workflow)
        # All steps should be in a single stage
        assert len(plan.stages) == 1
        assert sorted(plan.stages[0]) == ["a", "b", "c"]


class TestCsvOutput:
    """Tests for CSV output configuration parsing."""

    def test_csv_output_defaults(self):
        yaml_content = """
name: csv-test
description: test csv output
steps:
  - id: export
    prompt: "Generate data"
    csv_output:
      directory: ./results
"""
        workflow = parse_yaml_string(yaml_content)
        step = workflow.get_step("export")
        assert step.csv_output is not None
        assert step.csv_output.directory == "./results"
        assert step.csv_output.mode == "new_file"
        assert step.csv_output.filename == ""

    def test_csv_output_append_mode(self):
        yaml_content = """
name: csv-append
description: test append mode
steps:
  - id: collect
    prompt: "Collect data"
    csv_output:
      directory: /tmp/data
      mode: append
      filename: daily-report
"""
        workflow = parse_yaml_string(yaml_content)
        step = workflow.get_step("collect")
        assert step.csv_output is not None
        assert step.csv_output.directory == "/tmp/data"
        assert step.csv_output.mode == "append"
        assert step.csv_output.filename == "daily-report"

    def test_no_csv_output(self):
        yaml_content = """
name: no-csv
description: no csv
steps:
  - id: step1
    prompt: "No CSV"
"""
        workflow = parse_yaml_string(yaml_content)
        step = workflow.get_step("step1")
        assert step.csv_output is None

    def test_csv_output_with_other_configs(self):
        yaml_content = """
name: full-step
description: step with csv and retry
steps:
  - id: analyze
    prompt: "Analyze data"
    model: opus
    retry:
      max_attempts: 3
    csv_output:
      directory: ~/exports
      mode: new_file
"""
        workflow = parse_yaml_string(yaml_content)
        step = workflow.get_step("analyze")
        assert step.csv_output is not None
        assert step.csv_output.directory == "~/exports"
        assert step.retry is not None
        assert step.retry.max_attempts == 3
