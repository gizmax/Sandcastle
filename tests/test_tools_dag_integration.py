"""Tests for tool integration in DAG parser and validator."""

from __future__ import annotations

import pytest

from sandcastle.engine.dag import parse_yaml_string, validate


WORKFLOW_WITH_TOOLS = """
name: test-tools-workflow
description: A workflow that uses tools

default_model: sonnet
default_tools: [slack]

steps:
  - id: step1
    prompt: "Send a slack message"
  - id: step2
    depends_on: [step1]
    tools: [jira, github]
    prompt: "Create a jira issue based on {steps.step1.output}"
"""

WORKFLOW_WITH_UNKNOWN_TOOL = """
name: test-bad-tools
description: A workflow with unknown tool

default_model: sonnet

steps:
  - id: step1
    tools: [nonexistent_tool]
    prompt: "This should fail validation"
"""

WORKFLOW_WITHOUT_TOOLS = """
name: test-no-tools
description: A workflow without any tools

default_model: sonnet

steps:
  - id: step1
    prompt: "Just a simple step"
"""


class TestDagToolsParsing:
    """Tests for parsing tools field from YAML."""

    def test_parse_default_tools(self):
        wf = parse_yaml_string(WORKFLOW_WITH_TOOLS)
        assert wf.default_tools == ["slack"]

    def test_parse_step_tools(self):
        wf = parse_yaml_string(WORKFLOW_WITH_TOOLS)
        step2 = wf.get_step("step2")
        assert step2.tools == ["jira", "github"]

    def test_step_without_tools_is_none(self):
        wf = parse_yaml_string(WORKFLOW_WITH_TOOLS)
        step1 = wf.get_step("step1")
        assert step1.tools is None  # Inherits from default_tools at runtime

    def test_no_default_tools(self):
        wf = parse_yaml_string(WORKFLOW_WITHOUT_TOOLS)
        assert wf.default_tools == []

    def test_no_step_tools(self):
        wf = parse_yaml_string(WORKFLOW_WITHOUT_TOOLS)
        step1 = wf.get_step("step1")
        assert step1.tools is None


class TestDagToolsValidation:
    """Tests for tool validation in workflow validator."""

    def test_valid_tools_pass(self):
        wf = parse_yaml_string(WORKFLOW_WITH_TOOLS)
        errors = validate(wf)
        assert not any("tool" in e.lower() for e in errors)

    def test_unknown_tool_fails(self):
        wf = parse_yaml_string(WORKFLOW_WITH_UNKNOWN_TOOL)
        errors = validate(wf)
        tool_errors = [e for e in errors if "tool" in e.lower()]
        assert len(tool_errors) == 1
        assert "nonexistent_tool" in tool_errors[0]

    def test_backward_compat_no_tools(self):
        """Workflow without any tools should validate fine."""
        wf = parse_yaml_string(WORKFLOW_WITHOUT_TOOLS)
        errors = validate(wf)
        assert not any("tool" in e.lower() for e in errors)
