"""Comprehensive tests for Sandcastle tool registry and connector system (v27).

Covers: registry integrity, tool loading, credential resolution,
tool categories, parse_tool_ref, and schema generation.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.tools.credentials import (
    CREDENTIAL_PATTERNS,
    get_tool_credentials,
    mask_credential,
    validate_tool_credentials,
)
from sandcastle.engine.tools.loader import (
    bundle_tool_files,
    generate_tool_docs,
    generate_tool_schemas,
    generate_tool_schemas_json,
)
from sandcastle.engine.tools.registry import (
    KNOWN_TOOLS,
    TOOL_REGISTRY,
    VALID_CATEGORIES,
    ToolDefinition,
    ToolFunction,
    get_tool,
    list_tools,
    parse_tool_ref,
    validate_tools,
)

# Path to the connectors directory on disk
_CONNECTORS_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "engine" / "tools" / "connectors"


# ---------------------------------------------------------------------------
# 1. Registry integrity (20 tests)
# ---------------------------------------------------------------------------

class TestRegistryIntegrity:
    """Structural and consistency checks on the entire TOOL_REGISTRY."""

    def test_registry_has_at_least_62_tools(self):
        """The registry must contain at least 62 tool definitions."""
        assert len(TOOL_REGISTRY) >= 62

    def test_all_tool_names_are_unique(self):
        """Keys and the name field on each definition must match and be unique."""
        names = [tool.name for tool in TOOL_REGISTRY.values()]
        assert len(names) == len(set(names)), "Duplicate tool names detected"

    def test_registry_key_matches_tool_name(self):
        """Registry key must equal the ToolDefinition.name field."""
        for key, tool in TOOL_REGISTRY.items():
            assert key == tool.name, f"Key '{key}' != tool.name '{tool.name}'"

    def test_all_tools_have_a_category(self):
        """Every tool must have a non-empty category string."""
        for name, tool in TOOL_REGISTRY.items():
            assert tool.category, f"Tool '{name}' has empty category"

    def test_all_tools_have_a_description(self):
        """Every tool must have a non-empty description."""
        for name, tool in TOOL_REGISTRY.items():
            assert tool.description, f"Tool '{name}' has empty description"

    def test_all_categories_are_from_known_set(self):
        """Every tool category must be one of the VALID_CATEGORIES."""
        for name, tool in TOOL_REGISTRY.items():
            assert tool.category in VALID_CATEGORIES, (
                f"Tool '{name}' has unknown category '{tool.category}'. "
                f"Valid: {sorted(VALID_CATEGORIES)}"
            )

    def test_no_duplicate_tool_names_in_values(self):
        """Even if the dict prevents key dupes, verify value.name uniqueness."""
        seen: set[str] = set()
        for tool in TOOL_REGISTRY.values():
            assert tool.name not in seen, f"Duplicate tool name: {tool.name}"
            seen.add(tool.name)

    def test_get_tool_returns_correct_tool(self):
        """get_tool('slack') should return the Slack ToolDefinition."""
        tool = get_tool("slack")
        assert tool.name == "slack"
        assert tool.category == "communication"
        assert any(f.name == "send_message" for f in tool.functions)

    def test_get_tool_unknown_raises_key_error(self):
        """get_tool with a non-existent name must raise KeyError."""
        with pytest.raises(KeyError, match="Unknown tool 'absolutely_fake'"):
            get_tool("absolutely_fake")

    def test_list_tools_returns_all_tools(self):
        """list_tools() with no filter must return every registered tool."""
        tools = list_tools()
        assert len(tools) == len(TOOL_REGISTRY)

    def test_list_tools_sorted_by_name(self):
        """list_tools() output must be sorted alphabetically by name."""
        tools = list_tools()
        names = [t.name for t in tools]
        assert names == sorted(names)

    def test_list_tools_by_category_filters_correctly(self):
        """Filtering by category must only return matching tools."""
        for cat in VALID_CATEGORIES:
            tools = list_tools(category=cat)
            for t in tools:
                assert t.category == cat, (
                    f"Tool '{t.name}' has category '{t.category}' "
                    f"but was returned for category '{cat}'"
                )

    def test_known_tools_frozenset_matches_registry_keys(self):
        """KNOWN_TOOLS must be a frozenset identical to TOOL_REGISTRY.keys()."""
        assert isinstance(KNOWN_TOOLS, frozenset)
        assert KNOWN_TOOLS == frozenset(TOOL_REGISTRY.keys())

    def test_each_tool_has_credential_env_or_empty_list(self):
        """credential_env_vars must be a list (possibly empty) for every tool."""
        for name, tool in TOOL_REGISTRY.items():
            assert isinstance(tool.credential_env_vars, list), (
                f"Tool '{name}' credential_env_vars is not a list"
            )

    def test_credential_env_vars_follow_naming_convention(self):
        """All credential env vars should start with TOOL_ prefix."""
        for name, tool in TOOL_REGISTRY.items():
            for env_var in tool.credential_env_vars:
                assert env_var.startswith("TOOL_"), (
                    f"Tool '{name}' has env var '{env_var}' without TOOL_ prefix"
                )

    def test_all_tools_have_at_least_one_function(self):
        """Every tool must expose at least one callable function."""
        for name, tool in TOOL_REGISTRY.items():
            assert len(tool.functions) >= 1, f"Tool '{name}' has no functions"

    def test_all_functions_have_valid_parameter_schemas(self):
        """Each function's parameters dict must be a JSON Schema object type."""
        for name, tool in TOOL_REGISTRY.items():
            for func in tool.functions:
                assert isinstance(func.parameters, dict), (
                    f"{name}.{func.name}: parameters is not a dict"
                )
                assert func.parameters.get("type") == "object", (
                    f"{name}.{func.name}: parameters type must be 'object'"
                )

    def test_connector_file_exists_for_all_tools(self):
        """Every tool's connector_file must exist on disk."""
        for name, tool in TOOL_REGISTRY.items():
            filepath = _CONNECTORS_DIR / tool.connector_file
            assert filepath.exists(), (
                f"Tool '{name}' references connector '{tool.connector_file}' "
                f"which does not exist at {filepath}"
            )

    def test_connector_file_has_mjs_extension(self):
        """All connector files must end with .mjs."""
        for name, tool in TOOL_REGISTRY.items():
            assert tool.connector_file.endswith(".mjs"), (
                f"Tool '{name}' connector_file '{tool.connector_file}' "
                "does not end with .mjs"
            )

    def test_tool_definitions_are_frozen(self):
        """ToolDefinition instances should be frozen dataclasses."""
        tool = get_tool("slack")
        with pytest.raises(AttributeError):
            tool.name = "tampered"  # type: ignore


# ---------------------------------------------------------------------------
# 2. Tool loading (12 tests)
# ---------------------------------------------------------------------------

class TestToolLoading:
    """Tests for the loader module: bundling, docs, and schema generation."""

    def test_bundle_known_tool_returns_content(self):
        """Bundling a known tool returns its .mjs file content."""
        files = bundle_tool_files(["slack"])
        assert "slack.mjs" in files
        assert len(files["slack.mjs"]) > 0

    def test_bundle_multiple_tools(self):
        """Bundling multiple tools returns all their connector files."""
        files = bundle_tool_files(["slack", "jira", "github"])
        assert len(files) == 3
        assert all(fname.endswith(".mjs") for fname in files)

    def test_bundle_missing_tool_gracefully_skipped(self):
        """Unknown tool names are silently skipped in bundling."""
        files = bundle_tool_files(["nonexistent_tool_xyz"])
        assert files == {}

    def test_bundle_deduplicates_tool_names(self):
        """Passing the same tool twice should not duplicate files."""
        files = bundle_tool_files(["slack", "slack", "slack"])
        assert len(files) == 1
        assert "slack.mjs" in files

    def test_path_traversal_in_connector_file_rejected(self):
        """A tool with a path-traversal connector_file must not leak files."""
        fake_tool = ToolDefinition(
            name="evil_tool",
            description="Tries path traversal",
            category="general",
            functions=[
                ToolFunction(
                    name="exploit",
                    description="Evil function",
                    parameters={"type": "object", "properties": {}},
                ),
            ],
            credential_env_vars=[],
            connector_file="../../etc/passwd",
        )
        with patch.dict(TOOL_REGISTRY, {"evil_tool": fake_tool}):
            files = bundle_tool_files(["evil_tool"])
        # Path traversal should be blocked - file not included
        assert "../../etc/passwd" not in files

    def test_bundle_empty_list_returns_empty(self):
        """Empty tool list returns empty dict."""
        files = bundle_tool_files([])
        assert files == {}

    def test_all_registered_tools_bundleable(self):
        """Every tool in the registry should have a bundleable connector file."""
        all_names = list(TOOL_REGISTRY.keys())
        files = bundle_tool_files(all_names)
        assert len(files) == len(all_names)

    def test_generate_tool_docs_contains_header(self):
        """Generated docs must start with the standard header."""
        docs = generate_tool_docs(["slack"])
        assert "# Available Tools" in docs

    def test_generate_tool_docs_contains_cli_example(self):
        """Docs must include a Bash CLI invocation example."""
        docs = generate_tool_docs(["slack"])
        assert "node /home/user/tools/slack.mjs" in docs

    def test_generate_tool_schemas_openai_format(self):
        """Schemas must follow the OpenAI function_calling structure."""
        schemas = generate_tool_schemas(["slack"])
        assert len(schemas) > 0
        for schema in schemas:
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]

    def test_generate_tool_schemas_json_valid(self):
        """JSON schema output must be valid JSON."""
        json_str = generate_tool_schemas_json(["slack", "jira"])
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) > 0

    def test_generate_tool_schemas_deduplicates(self):
        """Passing the same tool twice should not produce duplicate schemas."""
        schemas_once = generate_tool_schemas(["slack"])
        schemas_twice = generate_tool_schemas(["slack", "slack"])
        assert len(schemas_once) == len(schemas_twice)


# ---------------------------------------------------------------------------
# 3. Tool credential resolution (12 tests)
# ---------------------------------------------------------------------------

class TestToolCredentialResolution:
    """Tests for credential fetching, validation, and masking."""

    def test_credential_from_env_variable(self, monkeypatch):
        """Credentials are read from environment variables."""
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "xoxb-live-token")
        creds = get_tool_credentials(["slack"])
        assert creds["TOOL_SLACK_BOT_TOKEN"] == "xoxb-live-token"

    def test_credential_missing_returns_empty(self, monkeypatch):
        """Missing env vars are omitted from the result."""
        monkeypatch.delenv("TOOL_SLACK_BOT_TOKEN", raising=False)
        creds = get_tool_credentials(["slack"])
        assert "TOOL_SLACK_BOT_TOKEN" not in creds

    def test_missing_credential_validate_clear_error(self, monkeypatch):
        """validate_tool_credentials reports which vars are missing."""
        monkeypatch.delenv("TOOL_JIRA_API_TOKEN", raising=False)
        monkeypatch.delenv("TOOL_JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("TOOL_JIRA_EMAIL", raising=False)
        result = validate_tool_credentials(["jira"])
        assert result["jira"]["configured"] is False
        assert "TOOL_JIRA_API_TOKEN" in result["jira"]["missing"]
        assert "TOOL_JIRA_BASE_URL" in result["jira"]["missing"]
        assert "TOOL_JIRA_EMAIL" in result["jira"]["missing"]

    def test_credential_masking_long_value(self):
        """Long credentials show first 4 + ... + last 4 chars."""
        masked = mask_credential("sk-abcdef123456xyz")
        assert masked == "sk-a...6xyz"  # first 4 + ... + last 4
        assert "abcdef" not in masked

    def test_credential_masking_short_value(self):
        """Short credentials (<=8 chars) are fully masked."""
        masked = mask_credential("abc")
        assert masked == "***"
        masked2 = mask_credential("12345678")
        assert masked2 == "********"

    def test_credential_masking_empty_value(self):
        """Empty string returns a fixed mask."""
        assert mask_credential("") == "****"

    def test_credential_masking_boundary_nine_chars(self):
        """9-char value should show first 4 and last 4 (with overlap)."""
        masked = mask_credential("123456789")
        assert masked == "1234...6789"

    def test_auto_generated_credential_policies_per_tool(self):
        """Each tool with credential_env_vars must have all vars prefixed TOOL_."""
        for name, tool in TOOL_REGISTRY.items():
            for var in tool.credential_env_vars:
                assert var.startswith("TOOL_"), (
                    f"Tool '{name}' env var '{var}' violates TOOL_ prefix policy"
                )
                # Env var should be UPPERCASE
                assert var == var.upper(), (
                    f"Tool '{name}' env var '{var}' is not uppercase"
                )

    def test_multiple_tools_credentials_merged(self, monkeypatch):
        """Credentials from multiple tools are merged into one dict."""
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "xoxb-tok")
        monkeypatch.setenv("TOOL_GITHUB_TOKEN", "ghp_abc")
        creds = get_tool_credentials(["slack", "github"])
        assert "TOOL_SLACK_BOT_TOKEN" in creds
        assert "TOOL_GITHUB_TOKEN" in creds

    def test_validate_unknown_tool_reports_error(self):
        """validate_tool_credentials for unknown tool includes error key."""
        result = validate_tool_credentials(["totally_unknown"])
        assert result["totally_unknown"]["configured"] is False
        assert "error" in result["totally_unknown"]

    def test_webhook_no_credentials_needed(self):
        """Webhook has an empty credential_env_vars list."""
        tool = get_tool("webhook")
        assert tool.credential_env_vars == []
        creds = get_tool_credentials(["webhook"])
        assert creds == {}

    def test_credential_patterns_are_valid_regex(self):
        """All CREDENTIAL_PATTERNS must compile as valid regex."""
        for pattern in CREDENTIAL_PATTERNS:
            try:
                re.compile(pattern)
            except re.error as exc:
                pytest.fail(f"Invalid regex in CREDENTIAL_PATTERNS: '{pattern}' - {exc}")


# ---------------------------------------------------------------------------
# 4. Tool categories (7 tests)
# ---------------------------------------------------------------------------

class TestToolCategories:
    """Tests for VALID_CATEGORIES and category-based filtering."""

    def test_all_categories_have_at_least_one_tool(self):
        """Every valid category must contain at least one registered tool."""
        for cat in VALID_CATEGORIES:
            tools = list_tools(category=cat)
            assert len(tools) >= 1, f"Category '{cat}' has no tools"

    def test_category_names_are_human_readable(self):
        """Category names should be lowercase, underscore-separated words."""
        for cat in VALID_CATEGORIES:
            assert cat == cat.lower(), f"Category '{cat}' is not lowercase"
            assert re.match(r"^[a-z][a-z0-9_]*$", cat), (
                f"Category '{cat}' contains invalid characters"
            )

    def test_get_tools_by_category_returns_non_empty(self):
        """list_tools(category=...) returns non-empty for each known category."""
        for cat in VALID_CATEGORIES:
            result = list_tools(category=cat)
            assert len(result) > 0, f"Category '{cat}' returned empty list"

    def test_unknown_category_returns_empty_list(self):
        """Filtering by a non-existent category returns empty list."""
        result = list_tools(category="quantum_computing")
        assert result == []

    def test_expected_categories_present(self):
        """Core categories must be present in VALID_CATEGORIES."""
        expected = {"communication", "project_management", "crm", "data", "general"}
        assert expected.issubset(VALID_CATEGORIES), (
            f"Missing core categories: {expected - VALID_CATEGORIES}"
        )

    def test_valid_categories_is_frozenset(self):
        """VALID_CATEGORIES must be a frozenset for immutability."""
        assert isinstance(VALID_CATEGORIES, frozenset)

    def test_communication_category_contains_slack(self):
        """The communication category must include Slack."""
        tools = list_tools(category="communication")
        names = {t.name for t in tools}
        assert "slack" in names


# ---------------------------------------------------------------------------
# 5. parse_tool_ref (10 tests)
# ---------------------------------------------------------------------------

class TestParseToolRef:
    """Tests for parse_tool_ref() which handles tool:connection syntax."""

    def test_plain_tool_name(self):
        """Simple tool name returns (name, None)."""
        name, conn = parse_tool_ref("slack")
        assert name == "slack"
        assert conn is None

    def test_named_connection(self):
        """Tool with connection name returns both parts."""
        name, conn = parse_tool_ref("postgresql:analytics")
        assert name == "postgresql"
        assert conn == "analytics"

    def test_empty_connection_name_raises(self):
        """Tool ref 'postgresql:' with empty connection must raise ValueError."""
        with pytest.raises(ValueError, match="Empty connection name"):
            parse_tool_ref("postgresql:")

    def test_invalid_connection_name_special_chars_raises(self):
        """Connection names with special characters must be rejected."""
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("postgresql:../../etc")

    def test_connection_name_with_hyphens_ok(self):
        """Hyphens in connection names are allowed."""
        name, conn = parse_tool_ref("postgresql:my-analytics-db")
        assert conn == "my-analytics-db"

    def test_connection_name_with_underscores_ok(self):
        """Underscores in connection names are allowed."""
        name, conn = parse_tool_ref("postgresql:prod_replica")
        assert conn == "prod_replica"

    def test_connection_name_starting_with_number_ok(self):
        """Connection names starting with digits are allowed."""
        name, conn = parse_tool_ref("redis:1primary")
        assert conn == "1primary"

    def test_connection_name_starting_with_hyphen_rejected(self):
        """Connection names must start with alphanumeric char."""
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("redis:-primary")

    def test_connection_name_max_length(self):
        """Connection names up to 100 chars should be accepted."""
        long_name = "a" * 100
        name, conn = parse_tool_ref(f"redis:{long_name}")
        assert conn == long_name

    def test_connection_name_over_max_length_rejected(self):
        """Connection names over 100 chars must be rejected."""
        too_long = "a" * 101
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref(f"redis:{too_long}")


# ---------------------------------------------------------------------------
# 6. validate_tools (5 tests)
# ---------------------------------------------------------------------------

class TestValidateTools:
    """Tests for the validate_tools() function."""

    def test_valid_plain_tools(self):
        """Known tool names produce no errors."""
        errors = validate_tools(["slack", "jira", "github"])
        assert errors == []

    def test_valid_named_connections(self):
        """Tool refs with valid connection names produce no errors."""
        errors = validate_tools(["postgresql:analytics", "redis:cache"])
        assert errors == []

    def test_unknown_tool_produces_error(self):
        """Unknown tool names generate exactly one error each."""
        errors = validate_tools(["fake_tool_abc"])
        assert len(errors) == 1
        assert "fake_tool_abc" in errors[0]

    def test_invalid_connection_name_produces_error(self):
        """Invalid connection name format generates an error."""
        errors = validate_tools(["postgresql:../../etc"])
        assert len(errors) == 1
        assert "Invalid connection name" in errors[0]

    def test_empty_list_produces_no_errors(self):
        """Empty input returns empty error list."""
        errors = validate_tools([])
        assert errors == []


# ---------------------------------------------------------------------------
# 7. Schema generation detail tests (5 tests)
# ---------------------------------------------------------------------------

class TestSchemaGeneration:
    """Detailed tests for OpenAI schema output and tool docs."""

    def test_schema_function_name_prefixed_by_tool(self):
        """Schema function names must be prefixed with tool_name underscore."""
        schemas = generate_tool_schemas(["slack"])
        for schema in schemas:
            func_name = schema["function"]["name"]
            assert func_name.startswith("slack_"), (
                f"Schema function '{func_name}' missing 'slack_' prefix"
            )

    def test_schema_description_includes_tool_name(self):
        """Schema description should contain [tool_name] prefix."""
        schemas = generate_tool_schemas(["jira"])
        for schema in schemas:
            desc = schema["function"]["description"]
            assert "[jira]" in desc

    def test_docs_include_parameter_details(self):
        """Generated docs should list parameter names and required/optional."""
        docs = generate_tool_docs(["slack"])
        assert "(required)" in docs or "(optional)" in docs

    def test_docs_unknown_tool_silently_skipped(self):
        """Unknown tools in doc generation are silently skipped."""
        docs = generate_tool_docs(["nonexistent_tool_xyz"])
        assert "nonexistent_tool_xyz" not in docs

    def test_schemas_empty_for_unknown_tools(self):
        """Schema generation for unknown tools returns empty list."""
        schemas = generate_tool_schemas(["nonexistent_tool_xyz"])
        assert schemas == []
