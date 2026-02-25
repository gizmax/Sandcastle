"""Tests for the Tool Loader - file bundling and doc/schema generation."""

from __future__ import annotations

import json

import pytest

from sandcastle.engine.tools.loader import (
    bundle_tool_files,
    generate_tool_docs,
    generate_tool_schemas,
    generate_tool_schemas_json,
)


class TestBundleToolFiles:
    """Tests for bundle_tool_files()."""

    def test_bundle_known_tool(self):
        files = bundle_tool_files(["slack"])
        assert "slack.mjs" in files
        assert "send_message" in files["slack.mjs"]

    def test_bundle_multiple_tools(self):
        files = bundle_tool_files(["slack", "jira", "github"])
        assert len(files) == 3
        assert "slack.mjs" in files
        assert "jira.mjs" in files
        assert "github.mjs" in files

    def test_bundle_unknown_tool(self):
        files = bundle_tool_files(["nonexistent"])
        assert files == {}

    def test_bundle_empty_list(self):
        files = bundle_tool_files([])
        assert files == {}

    def test_all_connectors_bundleable(self):
        from sandcastle.engine.tools.registry import TOOL_REGISTRY
        all_names = list(TOOL_REGISTRY.keys())
        files = bundle_tool_files(all_names)
        # All tools should have connector files
        assert len(files) == len(all_names)


class TestGenerateToolDocs:
    """Tests for generate_tool_docs()."""

    def test_docs_contain_tool_name(self):
        docs = generate_tool_docs(["slack"])
        assert "slack" in docs
        assert "send_message" in docs

    def test_docs_contain_cli_example(self):
        docs = generate_tool_docs(["slack"])
        assert "node /home/user/tools/slack.mjs" in docs

    def test_docs_contain_parameters(self):
        docs = generate_tool_docs(["slack"])
        assert "channel" in docs
        assert "text" in docs

    def test_docs_multiple_tools(self):
        docs = generate_tool_docs(["slack", "jira"])
        assert "## slack" in docs
        assert "## jira" in docs

    def test_docs_empty_list(self):
        docs = generate_tool_docs([])
        assert "# Available Tools" in docs

    def test_docs_header_present(self):
        docs = generate_tool_docs(["slack"])
        assert "# Available Tools" in docs
        assert "Bash" in docs  # Should mention Bash tool


class TestGenerateToolSchemas:
    """Tests for generate_tool_schemas() and generate_tool_schemas_json()."""

    def test_schemas_for_slack(self):
        schemas = generate_tool_schemas(["slack"])
        assert len(schemas) == 3  # send_message, list_channels, get_messages
        for s in schemas:
            assert s["type"] == "function"
            assert "function" in s
            assert s["function"]["name"].startswith("slack_")

    def test_schemas_function_format(self):
        schemas = generate_tool_schemas(["jira"])
        for s in schemas:
            func = s["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert func["description"].startswith("[jira]")

    def test_schemas_multiple_tools(self):
        schemas = generate_tool_schemas(["slack", "github"])
        slack_schemas = [s for s in schemas if s["function"]["name"].startswith("slack_")]
        github_schemas = [s for s in schemas if s["function"]["name"].startswith("github_")]
        assert len(slack_schemas) > 0
        assert len(github_schemas) > 0

    def test_schemas_json_valid(self):
        json_str = generate_tool_schemas_json(["slack"])
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) > 0

    def test_schemas_empty_list(self):
        schemas = generate_tool_schemas([])
        assert schemas == []
