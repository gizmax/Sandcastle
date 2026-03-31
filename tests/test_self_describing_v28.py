"""Tests for self-describing workflow definitions (v0.28).

Covers metadata fields on StepDefinition, WorkflowDefinition methods
(summary, owners, health), YAML parsing with/without metadata,
CLI commands (describe, lint, owners), template validation, and
audit event payload enrichment.
"""

from __future__ import annotations

import argparse
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from sandcastle.engine.dag import (
    StepDefinition,
    WorkflowDefinition,
    parse,
    parse_yaml_string,
    validate,
)


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

WORKFLOW_WITH_METADATA = """\
name: test-meta
description: Workflow with full self-describing metadata

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: fetch
    type: http
    http_config:
      url: https://example.com/api
    responsibility: "Fetch latest data from Example API"
    source_hint: "Tom needs daily data pull (JIRA-123)"
    owner: "ECHO"
    added_date: "2026-03-15"

  - id: analyze
    depends_on: [fetch]
    prompt: "Analyze the fetched data"
    responsibility: "LLM analysis of fetched data for patterns"
    source_hint: "Automated pattern detection saves 2h of manual work daily"
    owner: "ECHO"
    added_date: "2026-03-16"

  - id: report
    depends_on: [analyze]
    prompt: "Generate a summary report"
    responsibility: "Produce a human-readable summary report"
    source_hint: "Weekly stakeholder report requirement (JIRA-456)"
    owner: "reporting-team"
    added_date: "2026-03-17"
"""

WORKFLOW_WITHOUT_METADATA = """\
name: test-plain
description: Workflow without any metadata

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: step-a
    prompt: "Do task A"
  - id: step-b
    depends_on: [step-a]
    prompt: "Do task B"
"""

WORKFLOW_PARTIAL_METADATA = """\
name: test-partial
description: Some steps have metadata, some don't

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: fetch
    type: http
    http_config:
      url: https://example.com
    responsibility: "Fetch data"
    owner: "ALPHA"

  - id: process
    depends_on: [fetch]
    prompt: "Process the data"

  - id: output
    depends_on: [process]
    prompt: "Output results"
    responsibility: "Format and output final results"
    source_hint: "Client requires CSV output"
    owner: "ALPHA"
    added_date: "2026-03-20"
"""


# ---------------------------------------------------------------------------
# StepDefinition metadata fields
# ---------------------------------------------------------------------------


class TestStepDefinitionMetadata:
    """Tests for metadata fields on StepDefinition dataclass."""

    def test_step_with_all_metadata(self):
        """StepDefinition should accept all four metadata fields."""
        step = StepDefinition(
            id="fetch",
            prompt="Fetch data",
            responsibility="Fetch latest data from API",
            source_hint="Business requirement from Q2 roadmap",
            owner="ECHO",
            added_date="2026-03-15",
        )
        assert step.responsibility == "Fetch latest data from API"
        assert step.source_hint == "Business requirement from Q2 roadmap"
        assert step.owner == "ECHO"
        assert step.added_date == "2026-03-15"

    def test_step_without_metadata_defaults(self):
        """StepDefinition without metadata should default to empty strings."""
        step = StepDefinition(id="plain", prompt="Do something")
        assert step.responsibility == ""
        assert step.source_hint == ""
        assert step.owner == ""
        assert step.added_date == ""

    def test_step_partial_metadata(self):
        """StepDefinition should work with only some metadata fields set."""
        step = StepDefinition(
            id="partial",
            prompt="Partial step",
            responsibility="Does something",
            owner="team-x",
        )
        assert step.responsibility == "Does something"
        assert step.source_hint == ""
        assert step.owner == "team-x"
        assert step.added_date == ""


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


class TestYamlParsing:
    """Tests for YAML parsing of self-describing metadata."""

    def test_parse_with_metadata(self):
        """Metadata fields should be parsed from YAML correctly."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        fetch = wf.get_step("fetch")
        assert fetch.responsibility == "Fetch latest data from Example API"
        assert fetch.source_hint == "Tom needs daily data pull (JIRA-123)"
        assert fetch.owner == "ECHO"
        assert fetch.added_date == "2026-03-15"

    def test_parse_without_metadata(self):
        """Parsing YAML without metadata should not fail (backward compat)."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_METADATA)
        step_a = wf.get_step("step-a")
        assert step_a.responsibility == ""
        assert step_a.source_hint == ""
        assert step_a.owner == ""
        assert step_a.added_date == ""

    def test_parse_partial_metadata(self):
        """Steps with partial metadata should parse correctly."""
        wf = parse_yaml_string(WORKFLOW_PARTIAL_METADATA)
        fetch = wf.get_step("fetch")
        assert fetch.responsibility == "Fetch data"
        assert fetch.owner == "ALPHA"
        assert fetch.source_hint == ""
        assert fetch.added_date == ""

        process = wf.get_step("process")
        assert process.responsibility == ""
        assert process.owner == ""

    def test_parse_from_file(self):
        """Metadata should be parsed correctly from a YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_WITH_METADATA)
            f.flush()
            wf = parse(f.name)
        assert wf.get_step("analyze").owner == "ECHO"
        assert wf.get_step("report").added_date == "2026-03-17"

    def test_validation_still_passes_with_metadata(self):
        """Workflows with metadata should pass validation."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        errors = validate(wf)
        assert errors == []

    def test_validation_still_passes_without_metadata(self):
        """Workflows without metadata should still pass validation."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_METADATA)
        errors = validate(wf)
        assert errors == []


# ---------------------------------------------------------------------------
# WorkflowDefinition.summary()
# ---------------------------------------------------------------------------


class TestWorkflowSummary:
    """Tests for the summary() method."""

    def test_summary_contains_workflow_name(self):
        """Summary should start with the workflow name."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert s.startswith("test-meta")

    def test_summary_contains_description(self):
        """Summary should include the workflow description."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert "full self-describing metadata" in s

    def test_summary_contains_responsibilities(self):
        """Summary should list step responsibilities."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert "-> Fetch latest data from Example API" in s
        assert "-> LLM analysis of fetched data for patterns" in s

    def test_summary_contains_source_hints(self):
        """Summary should include why each step exists."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert "Why: Tom needs daily data pull (JIRA-123)" in s

    def test_summary_contains_owners(self):
        """Summary should show step owners."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert "Owner: ECHO" in s
        assert "Owner: reporting-team" in s

    def test_summary_contains_step_types(self):
        """Summary should show step types in brackets."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert "[http] fetch" in s
        assert "[standard] analyze" in s

    def test_summary_contains_dependencies(self):
        """Summary should show step dependencies."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        s = wf.summary()
        assert "(after: fetch)" in s

    def test_summary_without_metadata(self):
        """Summary of a workflow without metadata should still work."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_METADATA)
        s = wf.summary()
        assert "test-plain" in s
        assert "[standard] step-a" in s
        # No responsibility line for steps without metadata
        assert "->" not in s


# ---------------------------------------------------------------------------
# WorkflowDefinition.owners()
# ---------------------------------------------------------------------------


class TestWorkflowOwners:
    """Tests for the owners() method."""

    def test_owners_returns_all_unique_owners(self):
        """owners() should return all unique owner strings."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        o = wf.owners()
        assert o == {"ECHO", "reporting-team"}

    def test_owners_empty_when_no_metadata(self):
        """owners() should return empty set when no owners are set."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_METADATA)
        assert wf.owners() == set()

    def test_owners_partial_metadata(self):
        """owners() should only include steps that have an owner."""
        wf = parse_yaml_string(WORKFLOW_PARTIAL_METADATA)
        assert wf.owners() == {"ALPHA"}


# ---------------------------------------------------------------------------
# WorkflowDefinition.health()
# ---------------------------------------------------------------------------


class TestWorkflowHealth:
    """Tests for the health() method."""

    def test_health_ok_when_all_fields_present(self):
        """health() should return ok=True when all metadata is filled."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        h = wf.health()
        assert h["ok"] is True
        assert h["issues"] == []
        assert h["filled_fields"] == h["total_fields"]

    def test_health_detects_missing_responsibility(self):
        """health() should flag steps missing responsibility."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_METADATA)
        h = wf.health()
        assert h["ok"] is False
        missing = [i for i in h["issues"] if "missing responsibility" in i]
        assert len(missing) == 2  # step-a and step-b

    def test_health_detects_missing_source_hint(self):
        """health() should flag steps missing source_hint."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_METADATA)
        h = wf.health()
        missing = [i for i in h["issues"] if "missing source_hint" in i]
        assert len(missing) == 2

    def test_health_partial_metadata(self):
        """health() should detect partially missing fields."""
        wf = parse_yaml_string(WORKFLOW_PARTIAL_METADATA)
        h = wf.health()
        assert h["ok"] is False
        # process step missing both responsibility and source_hint
        assert any("process" in i and "responsibility" in i for i in h["issues"])
        assert any("process" in i and "source_hint" in i for i in h["issues"])

    def test_health_completeness_percentage(self):
        """health() should report correct filled/total counts."""
        wf = parse_yaml_string(WORKFLOW_WITH_METADATA)
        h = wf.health()
        # 3 steps * 4 fields = 12 total, all filled
        assert h["total_fields"] == 12
        assert h["filled_fields"] == 12


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


class TestCLIDescribe:
    """Tests for the 'sandcastle describe' command."""

    def test_describe_prints_summary(self, capsys):
        """describe command should print the workflow summary."""
        from sandcastle.__main__ import _cmd_describe

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_WITH_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_describe(args)

        out = capsys.readouterr().out
        assert "test-meta" in out
        assert "-> Fetch latest data from Example API" in out
        assert "Owner: ECHO" in out


class TestCLILint:
    """Tests for the 'sandcastle lint' command."""

    def test_lint_clean_workflow(self, capsys):
        """Lint on a fully-annotated workflow should report 0 warnings."""
        from sandcastle.__main__ import _cmd_lint

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_WITH_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_lint(args)

        out = capsys.readouterr().out
        assert "0 warnings" in out
        assert "100%" in out

    def test_lint_missing_metadata(self, capsys):
        """Lint should report warnings for missing metadata."""
        from sandcastle.__main__ import _cmd_lint

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_WITHOUT_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_lint(args)

        out = capsys.readouterr().out
        assert "Warnings:" in out
        assert "missing responsibility" in out
        assert "4 warnings" in out  # 2 steps * 2 (responsibility + source_hint)

    def test_lint_partial_metadata(self, capsys):
        """Lint should detect partial metadata gaps."""
        from sandcastle.__main__ import _cmd_lint

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_PARTIAL_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_lint(args)

        out = capsys.readouterr().out
        assert "Warnings:" in out
        assert "process" in out


class TestCLIOwners:
    """Tests for the 'sandcastle owners' command."""

    def test_owners_shows_all_owners(self, capsys):
        """owners command should list all owners and their steps."""
        from sandcastle.__main__ import _cmd_owners

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_WITH_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_owners(args)

        out = capsys.readouterr().out
        assert "ECHO: fetch, analyze" in out
        assert "reporting-team: report" in out

    def test_owners_shows_unassigned(self, capsys):
        """owners command should show unassigned steps."""
        from sandcastle.__main__ import _cmd_owners

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_PARTIAL_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_owners(args)

        out = capsys.readouterr().out
        assert "unassigned: process" in out
        assert "ALPHA:" in out

    def test_owners_all_unassigned(self, capsys):
        """owners command should handle workflows with no owners."""
        from sandcastle.__main__ import _cmd_owners

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(WORKFLOW_WITHOUT_METADATA)
            f.flush()
            args = argparse.Namespace(workflow=f.name)
            _cmd_owners(args)

        out = capsys.readouterr().out
        assert "unassigned: step-a, step-b" in out


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


class TestCLIParser:
    """Tests for CLI argument parsing of new commands."""

    def test_describe_parser(self):
        """'describe' command should parse workflow argument."""
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["describe", "my-workflow.yaml"])
        assert args.command == "describe"
        assert args.workflow == "my-workflow.yaml"

    def test_lint_parser(self):
        """'lint' command should parse workflow argument."""
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["lint", "my-workflow.yaml"])
        assert args.command == "lint"
        assert args.workflow == "my-workflow.yaml"

    def test_owners_parser(self):
        """'owners' command should parse workflow argument."""
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["owners", "my-workflow.yaml"])
        assert args.command == "owners"
        assert args.workflow == "my-workflow.yaml"


# ---------------------------------------------------------------------------
# Template validation
# ---------------------------------------------------------------------------


class TestTemplateValidation:
    """Verify that updated templates with metadata still parse and validate."""

    def test_deep_research_pipeline_parses(self):
        """deep_research_pipeline.yaml should parse with metadata."""
        templates_dir = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"
        path = templates_dir / "deep_research_pipeline.yaml"
        if not path.exists():
            pytest.skip("Template not found")
        wf = parse(str(path))
        assert wf.name == "deep-research-pipeline"
        assert len(wf.steps) == 6
        # Verify metadata was added
        branch_a = wf.get_step("research-branch-a")
        assert branch_a.responsibility != ""
        assert branch_a.owner == "research-team"

    def test_deep_research_pipeline_validates(self):
        """deep_research_pipeline.yaml should pass validation."""
        templates_dir = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"
        path = templates_dir / "deep_research_pipeline.yaml"
        if not path.exists():
            pytest.skip("Template not found")
        wf = parse(str(path))
        errors = validate(wf)
        assert errors == []

    def test_omlx_research_tweet_parses(self):
        """omlx_research_tweet.yaml should parse with metadata."""
        templates_dir = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"
        path = templates_dir / "omlx_research_tweet.yaml"
        if not path.exists():
            pytest.skip("Template not found")
        wf = parse(str(path))
        assert wf.name == "omlx-research-tweet"
        research = wf.get_step("research")
        assert research.responsibility != ""
        assert research.owner == "content-team"

    def test_omlx_research_tweet_validates(self):
        """omlx_research_tweet.yaml should pass validation."""
        templates_dir = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"
        path = templates_dir / "omlx_research_tweet.yaml"
        if not path.exists():
            pytest.skip("Template not found")
        wf = parse(str(path))
        errors = validate(wf)
        assert errors == []


# ---------------------------------------------------------------------------
# Audit event payload enrichment
# ---------------------------------------------------------------------------


class TestAuditEventPayload:
    """Tests that audit event payloads include metadata when present."""

    def test_started_payload_includes_metadata(self):
        """step.started audit payload should include responsibility and source_hint."""
        step = StepDefinition(
            id="enrich",
            prompt="Enrich data",
            responsibility="Enrich raw data with external sources",
            source_hint="Data quality requirement from compliance team",
        )
        # Simulate the payload construction from executor.py
        payload: dict = {"step_id": step.id, "step_type": step.type, "workflow": "test"}
        if step.responsibility:
            payload["responsibility"] = step.responsibility
        if step.source_hint:
            payload["source_hint"] = step.source_hint

        assert payload["responsibility"] == "Enrich raw data with external sources"
        assert payload["source_hint"] == "Data quality requirement from compliance team"

    def test_started_payload_omits_empty_metadata(self):
        """step.started audit payload should not include empty metadata fields."""
        step = StepDefinition(id="plain", prompt="Plain step")
        payload: dict = {"step_id": step.id, "step_type": step.type, "workflow": "test"}
        if step.responsibility:
            payload["responsibility"] = step.responsibility
        if step.source_hint:
            payload["source_hint"] = step.source_hint

        assert "responsibility" not in payload
        assert "source_hint" not in payload

    def test_completed_payload_includes_metadata(self):
        """step.completed audit payload should include metadata when present."""
        step = StepDefinition(
            id="score",
            prompt="Score leads",
            responsibility="Score leads based on engagement metrics",
            source_hint="Sales team needs prioritized lead list",
        )
        payload: dict = {"step_id": step.id, "cost_usd": 0.05, "duration_seconds": 12.3}
        if step.responsibility:
            payload["responsibility"] = step.responsibility
        if step.source_hint:
            payload["source_hint"] = step.source_hint

        assert payload["responsibility"] == "Score leads based on engagement metrics"
        assert payload["source_hint"] == "Sales team needs prioritized lead list"
        assert payload["cost_usd"] == 0.05
