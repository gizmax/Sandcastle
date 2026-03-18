"""Tests for the Tool Registry."""

from __future__ import annotations

import pytest

from sandcastle.engine.tools.registry import (
    KNOWN_TOOLS,
    TOOL_REGISTRY,
    ToolDefinition,
    ToolFunction,
    get_tool,
    list_tools,
    validate_tools,
)


class TestToolRegistry:
    """Tests for the TOOL_REGISTRY dict and helper functions."""

    def test_registry_not_empty(self):
        assert len(TOOL_REGISTRY) >= 50

    def test_known_tools_matches_registry(self):
        assert KNOWN_TOOLS == frozenset(TOOL_REGISTRY.keys())

    def test_all_tools_have_required_fields(self):
        for name, tool in TOOL_REGISTRY.items():
            assert isinstance(tool, ToolDefinition)
            assert tool.name == name
            assert tool.description
            assert tool.category in (
                "communication", "project_management", "crm", "data",
                "general", "erp", "payments", "ai", "devops",
                "observability", "storage",
            )
            assert tool.connector_file.endswith(".mjs")
            assert len(tool.functions) > 0

    def test_all_functions_have_schemas(self):
        for name, tool in TOOL_REGISTRY.items():
            for func in tool.functions:
                assert isinstance(func, ToolFunction)
                assert func.name
                assert func.description
                assert isinstance(func.parameters, dict)
                assert func.parameters.get("type") == "object"

    def test_expected_tools_present(self):
        expected = {
            "slack", "jira", "github", "gmail", "notion", "hubspot",
            "salesforce", "zendesk", "teams", "gdrive", "postgresql", "webhook",
            "openai", "anthropic", "discord", "google-sheets", "airtable",
            "linear", "aws-s3", "redis", "supabase", "pinecone", "resend",
            "vercel", "cloudflare-workers", "firecrawl", "tavily", "elevenlabs",
            "zapier", "shopify", "quickbooks", "calendly", "whatsapp", "figma",
            "datadog", "plaid", "docusign", "pagerduty", "mcp-bridge",
            "human-input", "filesystem", "shell", "python-runtime",
            "code-interpreter",
        }
        assert expected.issubset(KNOWN_TOOLS)


class TestGetTool:
    """Tests for get_tool()."""

    def test_get_known_tool(self):
        tool = get_tool("slack")
        assert tool.name == "slack"
        assert tool.category == "communication"

    def test_get_unknown_tool_raises(self):
        with pytest.raises(KeyError, match="Unknown tool 'nonexistent'"):
            get_tool("nonexistent")

    def test_get_tool_returns_frozen(self):
        tool = get_tool("jira")
        # Frozen dataclass - should not be modifiable
        with pytest.raises(AttributeError):
            tool.name = "hacked"  # type: ignore


class TestListTools:
    """Tests for list_tools()."""

    def test_list_all(self):
        tools = list_tools()
        assert len(tools) >= 50
        # Sorted by name
        names = [t.name for t in tools]
        assert names == sorted(names)

    def test_list_by_category(self):
        comm_tools = list_tools(category="communication")
        assert all(t.category == "communication" for t in comm_tools)
        assert any(t.name == "slack" for t in comm_tools)

    def test_list_nonexistent_category(self):
        tools = list_tools(category="quantum_computing")
        assert tools == []


class TestValidateTools:
    """Tests for validate_tools()."""

    def test_valid_tools(self):
        errors = validate_tools(["slack", "jira"])
        assert errors == []

    def test_unknown_tool(self):
        errors = validate_tools(["slack", "nonexistent"])
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_empty_list(self):
        errors = validate_tools([])
        assert errors == []

    def test_all_tools_valid(self):
        errors = validate_tools(list(KNOWN_TOOLS))
        assert errors == []
