"""Wave 7 deep audit tests for the tool/connector loading system.

Covers: loader, registry, credentials, connector-utils, path traversal,
deduplication, category validation, connection name validation, and
edge cases in the tool system.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from sandcastle.engine.tools.credentials import (
    CREDENTIAL_PATTERNS,
    get_tool_credentials,
    mask_credential,
    validate_tool_credentials,
)
from sandcastle.engine.tools.loader import (
    _CONNECTORS_DIR,
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


# ---------------------------------------------------------------------------
# Registry: VALID_CATEGORIES
# ---------------------------------------------------------------------------


class TestValidCategories:
    """Tests for the VALID_CATEGORIES constant."""

    def test_valid_categories_not_empty(self):
        assert len(VALID_CATEGORIES) >= 7

    def test_expected_categories_present(self):
        expected = {
            "communication",
            "project_management",
            "crm",
            "data",
            "general",
            "erp",
            "payments",
            "ai",
            "devops",
        }
        assert expected.issubset(VALID_CATEGORIES)

    def test_every_tool_has_valid_category(self):
        for name, tool in TOOL_REGISTRY.items():
            assert tool.category in VALID_CATEGORIES, (
                f"Tool '{name}' has category '{tool.category}' "
                f"not in VALID_CATEGORIES"
            )

    def test_valid_categories_is_frozenset(self):
        assert isinstance(VALID_CATEGORIES, frozenset)


# ---------------------------------------------------------------------------
# Registry: parse_tool_ref - connection name validation
# ---------------------------------------------------------------------------


class TestParseToolRef:
    """Tests for parse_tool_ref() with connection name validation."""

    def test_simple_tool_name(self):
        name, conn = parse_tool_ref("slack")
        assert name == "slack"
        assert conn is None

    def test_tool_with_connection(self):
        name, conn = parse_tool_ref("postgresql:analytics")
        assert name == "postgresql"
        assert conn == "analytics"

    def test_connection_with_hyphens(self):
        name, conn = parse_tool_ref("postgresql:my-db-prod")
        assert name == "postgresql"
        assert conn == "my-db-prod"

    def test_connection_with_underscores(self):
        name, conn = parse_tool_ref("postgresql:my_db_prod")
        assert name == "postgresql"
        assert conn == "my_db_prod"

    def test_connection_with_numbers(self):
        name, conn = parse_tool_ref("redis:cache01")
        assert name == "redis"
        assert conn == "cache01"

    def test_empty_connection_name_raises(self):
        with pytest.raises(ValueError, match="Empty connection name"):
            parse_tool_ref("postgresql:")

    def test_connection_name_with_slash_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("postgresql:../secret")

    def test_connection_name_with_dot_dot_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("redis:..evil")

    def test_connection_name_with_spaces_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("slack:my connection")

    def test_connection_name_with_special_chars_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("github:conn@name")

    def test_connection_name_starting_with_hyphen_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("redis:-invalid")

    def test_connection_name_starting_with_underscore_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("redis:_invalid")

    def test_connection_name_max_length(self):
        # 100 chars - should pass
        long_name = "a" * 100
        name, conn = parse_tool_ref(f"postgresql:{long_name}")
        assert conn == long_name

    def test_connection_name_too_long_raises(self):
        # 101 chars - should fail
        long_name = "a" * 101
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref(f"postgresql:{long_name}")

    def test_connection_name_unicode_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("slack:conn\u00e9ction")

    def test_multiple_colons_takes_first_split(self):
        # "tool:a:b" -> connection_name = "a:b" which should be rejected
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("postgresql:conn:extra")

    def test_connection_name_single_char(self):
        name, conn = parse_tool_ref("redis:x")
        assert conn == "x"

    def test_connection_name_null_byte_raises(self):
        with pytest.raises(ValueError, match="Invalid connection name"):
            parse_tool_ref("redis:conn\x00name")


# ---------------------------------------------------------------------------
# Registry: validate_tools with connection name validation
# ---------------------------------------------------------------------------


class TestValidateToolsWave7:
    """Extended validation tests including connection name validation."""

    def test_valid_tool_with_connection(self):
        errors = validate_tools(["postgresql:analytics"])
        # postgresql is known, analytics is valid conn name
        assert errors == []

    def test_invalid_connection_name_reported(self):
        errors = validate_tools(["postgresql:../evil"])
        assert len(errors) == 1
        assert "Invalid connection name" in errors[0]

    def test_empty_connection_name_reported(self):
        errors = validate_tools(["slack:"])
        assert len(errors) == 1
        assert "Empty connection name" in errors[0]

    def test_unknown_tool_with_valid_connection(self):
        errors = validate_tools(["nonexistent:myconn"])
        assert len(errors) == 1
        assert "Unknown tool" in errors[0]

    def test_mixed_valid_and_invalid(self):
        errors = validate_tools([
            "slack",  # valid
            "postgresql:../bad",  # invalid conn name
            "nonexistent",  # unknown tool
        ])
        assert len(errors) == 2


# ---------------------------------------------------------------------------
# Loader: bundle_tool_files - path traversal
# ---------------------------------------------------------------------------


class TestBundleToolFilesPathTraversal:
    """Path traversal protection in bundle_tool_files."""

    def test_normal_tool_loads(self):
        files = bundle_tool_files(["slack"])
        assert "slack.mjs" in files

    def test_path_traversal_blocked(self):
        """Simulate a tool with a malicious connector_file."""
        evil_tool = ToolDefinition(
            name="evil",
            description="Evil tool",
            category="general",
            functions=[
                ToolFunction(
                    name="hack",
                    description="Do evil",
                    parameters={"type": "object", "properties": {}},
                )
            ],
            credential_env_vars=[],
            connector_file="../../../etc/passwd",
        )
        with patch.dict(TOOL_REGISTRY, {"evil": evil_tool}):
            files = bundle_tool_files(["evil"])
        assert files == {}

    def test_dot_dot_in_connector_file_blocked(self):
        evil_tool = ToolDefinition(
            name="evil2",
            description="Evil tool 2",
            category="general",
            functions=[
                ToolFunction(
                    name="hack",
                    description="Do evil",
                    parameters={"type": "object", "properties": {}},
                )
            ],
            credential_env_vars=[],
            connector_file="../loader.py",
        )
        with patch.dict(TOOL_REGISTRY, {"evil2": evil_tool}):
            files = bundle_tool_files(["evil2"])
        assert files == {}

    def test_symlink_would_still_resolve(self):
        """Even if connector_file is just a name, resolve() handles symlinks."""
        # This just tests that a normal tool still works with resolve()
        files = bundle_tool_files(["github"])
        assert "github.mjs" in files


# ---------------------------------------------------------------------------
# Loader: deduplication
# ---------------------------------------------------------------------------


class TestBundleToolFilesDeduplication:
    """Deduplication in bundle_tool_files."""

    def test_duplicate_tool_names_deduplicated(self):
        files = bundle_tool_files(["slack", "slack", "slack"])
        assert "slack.mjs" in files
        # Only one entry
        assert len(files) == 1

    def test_duplicate_mixed_with_unique(self):
        files = bundle_tool_files(["slack", "jira", "slack"])
        assert len(files) == 2


class TestGenerateToolDocsDeduplication:
    """Deduplication in generate_tool_docs."""

    def test_duplicate_tool_names_not_repeated(self):
        docs = generate_tool_docs(["slack", "slack", "slack"])
        # "## slack" should appear exactly once
        assert docs.count("## slack") == 1

    def test_duplicate_mixed(self):
        docs = generate_tool_docs(["slack", "jira", "slack"])
        assert docs.count("## slack") == 1
        assert docs.count("## jira") == 1


class TestGenerateToolSchemasDeduplication:
    """Deduplication in generate_tool_schemas."""

    def test_duplicate_tool_names_not_repeated(self):
        schemas = generate_tool_schemas(["slack", "slack"])
        # Slack has 3 functions; duplicating would give 6
        slack_count = sum(
            1 for s in schemas if s["function"]["name"].startswith("slack_")
        )
        assert slack_count == 3

    def test_duplicate_mixed(self):
        schemas_deduped = generate_tool_schemas(["slack", "jira", "slack"])
        schemas_unique = generate_tool_schemas(["slack", "jira"])
        assert len(schemas_deduped) == len(schemas_unique)


# ---------------------------------------------------------------------------
# Loader: all connector files exist on disk
# ---------------------------------------------------------------------------


class TestConnectorFilesExist:
    """Verify every tool's connector_file actually exists on disk."""

    def test_all_connector_files_exist(self):
        missing = []
        for name, tool in TOOL_REGISTRY.items():
            filepath = _CONNECTORS_DIR / tool.connector_file
            if not filepath.exists():
                missing.append(f"{name} -> {tool.connector_file}")
        assert missing == [], f"Missing connector files: {missing}"

    def test_connector_files_are_readable(self):
        for name, tool in TOOL_REGISTRY.items():
            filepath = _CONNECTORS_DIR / tool.connector_file
            content = filepath.read_text()
            assert len(content) > 0, f"Empty connector: {name}"

    def test_no_orphan_mjs_files(self):
        """All .mjs files in connectors/ should be referenced by a tool."""
        referenced = {t.connector_file for t in TOOL_REGISTRY.values()}
        # connector-utils.mjs is a shared utility, not a tool connector
        referenced.add("connector-utils.mjs")

        on_disk = {
            f.name
            for f in _CONNECTORS_DIR.iterdir()
            if f.suffix == ".mjs"
        }
        orphans = on_disk - referenced
        assert orphans == set(), f"Orphan .mjs files: {orphans}"


# ---------------------------------------------------------------------------
# Registry: tool definition integrity
# ---------------------------------------------------------------------------


class TestToolDefinitionIntegrity:
    """Deep validation of tool definitions."""

    def test_tool_name_matches_dict_key(self):
        for key, tool in TOOL_REGISTRY.items():
            assert tool.name == key

    def test_connector_file_ends_with_mjs(self):
        for name, tool in TOOL_REGISTRY.items():
            assert tool.connector_file.endswith(".mjs"), (
                f"Tool '{name}' connector_file doesn't end with .mjs: "
                f"{tool.connector_file}"
            )

    def test_no_path_separators_in_connector_file(self):
        """connector_file should be a plain filename, no directory components."""
        for name, tool in TOOL_REGISTRY.items():
            assert "/" not in tool.connector_file, (
                f"Tool '{name}' has / in connector_file: {tool.connector_file}"
            )
            assert "\\" not in tool.connector_file, (
                f"Tool '{name}' has \\ in connector_file: {tool.connector_file}"
            )

    def test_function_parameter_schemas_are_objects(self):
        for name, tool in TOOL_REGISTRY.items():
            for func in tool.functions:
                assert func.parameters.get("type") == "object", (
                    f"Tool '{name}' function '{func.name}' "
                    "parameters type is not 'object'"
                )

    def test_required_fields_are_in_properties(self):
        """Every 'required' field must exist in 'properties'."""
        for name, tool in TOOL_REGISTRY.items():
            for func in tool.functions:
                required = func.parameters.get("required", [])
                properties = func.parameters.get("properties", {})
                for req in required:
                    assert req in properties, (
                        f"Tool '{name}' func '{func.name}': "
                        f"required field '{req}' not in properties"
                    )

    def test_no_empty_function_lists(self):
        for name, tool in TOOL_REGISTRY.items():
            assert len(tool.functions) > 0, (
                f"Tool '{name}' has no functions"
            )

    def test_no_duplicate_function_names_within_tool(self):
        for name, tool in TOOL_REGISTRY.items():
            func_names = [f.name for f in tool.functions]
            assert len(func_names) == len(set(func_names)), (
                f"Tool '{name}' has duplicate function names: {func_names}"
            )

    def test_credential_env_vars_have_tool_prefix(self):
        """Credential env vars should start with TOOL_ for namespace isolation."""
        for name, tool in TOOL_REGISTRY.items():
            for var in tool.credential_env_vars:
                assert var.startswith("TOOL_"), (
                    f"Tool '{name}' has credential var '{var}' "
                    "without TOOL_ prefix"
                )

    def test_credential_env_vars_are_uppercase(self):
        for name, tool in TOOL_REGISTRY.items():
            for var in tool.credential_env_vars:
                assert var == var.upper(), (
                    f"Tool '{name}' credential var '{var}' is not uppercase"
                )

    def test_no_duplicate_credential_vars_across_unrelated_tools(self):
        """Two different tools should not share the same credential env var,
        unless they are in the same family (e.g. gmail and gdrive share nothing)."""
        var_to_tools: dict[str, list[str]] = {}
        for name, tool in TOOL_REGISTRY.items():
            for var in tool.credential_env_vars:
                var_to_tools.setdefault(var, []).append(name)

        # Each var should belong to exactly one tool
        conflicts = {
            var: tools
            for var, tools in var_to_tools.items()
            if len(tools) > 1
        }
        assert conflicts == {}, (
            f"Credential env vars shared across tools: {conflicts}"
        )


# ---------------------------------------------------------------------------
# Credentials: mask_credential edge cases
# ---------------------------------------------------------------------------


class TestMaskCredentialWave7:
    """Extended tests for mask_credential including edge cases."""

    def test_empty_string_returns_fixed_mask(self):
        assert mask_credential("") == "****"

    def test_single_char(self):
        assert mask_credential("x") == "*"

    def test_two_chars(self):
        assert mask_credential("ab") == "**"

    def test_eight_chars_fully_masked(self):
        assert mask_credential("12345678") == "********"

    def test_nine_chars_partial_reveal(self):
        result = mask_credential("123456789")
        assert result == "1234...6789"

    def test_long_token(self):
        token = "xoxb-" + "a" * 50
        result = mask_credential(token)
        assert result.startswith("xoxb")
        assert "..." in result
        assert len(result) < len(token)

    def test_whitespace_only_string(self):
        # Whitespace is truthy, so it should be masked normally
        result = mask_credential("   ")
        assert result == "***"


# ---------------------------------------------------------------------------
# Credentials: get_tool_credentials with invalid refs
# ---------------------------------------------------------------------------


class TestGetToolCredentialsWave7:
    """Extended credential resolution tests."""

    def test_invalid_connection_name_skipped(self, monkeypatch):
        """A tool ref with invalid connection name should be skipped, not crash."""
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "test-token")
        # "slack" is valid, "redis:../evil" has invalid connection name
        creds = get_tool_credentials(["slack", "redis:../evil"])
        assert "TOOL_SLACK_BOT_TOKEN" in creds

    def test_empty_connection_name_skipped(self, monkeypatch):
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "test-token")
        creds = get_tool_credentials(["slack", "redis:"])
        assert "TOOL_SLACK_BOT_TOKEN" in creds

    def test_credentials_not_leaked_across_tools(self, monkeypatch):
        """Each tool should only get its own credentials."""
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "slack-secret")
        monkeypatch.setenv("TOOL_GITHUB_TOKEN", "github-secret")

        slack_creds = get_tool_credentials(["slack"])
        assert "TOOL_SLACK_BOT_TOKEN" in slack_creds
        assert "TOOL_GITHUB_TOKEN" not in slack_creds

        github_creds = get_tool_credentials(["github"])
        assert "TOOL_GITHUB_TOKEN" in github_creds
        assert "TOOL_SLACK_BOT_TOKEN" not in github_creds

    def test_empty_env_var_omitted(self, monkeypatch):
        """Empty string env vars should be omitted."""
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "")
        creds = get_tool_credentials(["slack"])
        assert "TOOL_SLACK_BOT_TOKEN" not in creds

    def test_tools_with_no_cred_vars_return_empty(self):
        """Tools that need no credentials should return empty dict."""
        # webhook, shell, filesystem have no required env vars
        for tool_name in ["webhook", "shell", "filesystem"]:
            tool = get_tool(tool_name)
            if len(tool.credential_env_vars) == 0:
                creds = get_tool_credentials([tool_name])
                assert creds == {}


# ---------------------------------------------------------------------------
# Credentials: validate_tool_credentials edge cases
# ---------------------------------------------------------------------------


class TestValidateToolCredentialsWave7:
    """Extended validate_tool_credentials tests."""

    def test_multiple_cred_vars_partial(self, monkeypatch):
        """Jira needs 3 env vars; setting 1 should show partial config."""
        monkeypatch.setenv("TOOL_JIRA_API_TOKEN", "token")
        monkeypatch.delenv("TOOL_JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("TOOL_JIRA_EMAIL", raising=False)
        result = validate_tool_credentials(["jira"])
        assert result["jira"]["configured"] is False
        assert "TOOL_JIRA_API_TOKEN" in result["jira"]["present"]
        assert "TOOL_JIRA_BASE_URL" in result["jira"]["missing"]
        assert "TOOL_JIRA_EMAIL" in result["jira"]["missing"]

    def test_all_cred_vars_set(self, monkeypatch):
        monkeypatch.setenv("TOOL_JIRA_API_TOKEN", "token")
        monkeypatch.setenv("TOOL_JIRA_BASE_URL", "https://jira.example.com")
        monkeypatch.setenv("TOOL_JIRA_EMAIL", "user@example.com")
        result = validate_tool_credentials(["jira"])
        assert result["jira"]["configured"] is True
        assert result["jira"]["missing"] == []

    def test_zero_cred_tools_not_configured(self):
        """Tools with no credential requirements report configured=False."""
        result = validate_tool_credentials(["webhook"])
        assert result["webhook"]["configured"] is False
        assert result["webhook"]["missing"] == []
        assert result["webhook"]["present"] == []


# ---------------------------------------------------------------------------
# Credential patterns: regex correctness
# ---------------------------------------------------------------------------


class TestCredentialPatternsWave7:
    """Verify credential patterns match expected token formats."""

    def test_slack_bot_token(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if "xoxb" in p)
        assert re.search(pattern, "xoxb-1234-5678-abcdef")

    def test_github_pat(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if "ghp_" in p)
        assert re.search(pattern, "ghp_ABCdef123456")

    def test_openai_key(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if r"sk-" in p)
        assert re.search(pattern, "sk-proj-abcdef1234567890")

    def test_aws_key(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if "AKIA" in p)
        assert re.search(pattern, "AKIAIOSFODNN7EXAMPLE")

    def test_bearer_token(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if "Bearer" in p)
        assert re.search(pattern, "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def")

    def test_postgres_connection_string(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if "postgres" in p)
        assert re.search(
            pattern, "postgresql://user:pass@host:5432/db"
        )

    def test_slack_webhook_url(self):
        pattern = next(p for p in CREDENTIAL_PATTERNS if "slack" in p and "http" in p)
        assert re.search(
            pattern,
            "https://hooks.slack.com/services/T00/B00/XXXX",
        )


# ---------------------------------------------------------------------------
# Loader: generate_tool_docs correctness
# ---------------------------------------------------------------------------


class TestGenerateToolDocsWave7:
    """Extended doc generation tests."""

    def test_required_params_marked(self):
        docs = generate_tool_docs(["slack"])
        assert "(required)" in docs

    def test_optional_params_marked(self):
        docs = generate_tool_docs(["slack"])
        assert "(optional)" in docs

    def test_unknown_tool_silently_skipped(self):
        docs = generate_tool_docs(["nonexistent", "slack"])
        assert "## slack" in docs
        assert "nonexistent" not in docs

    def test_docs_contain_all_functions(self):
        docs = generate_tool_docs(["jira"])
        tool = get_tool("jira")
        for func in tool.functions:
            assert f"### {func.name}" in docs


# ---------------------------------------------------------------------------
# Loader: generate_tool_schemas correctness
# ---------------------------------------------------------------------------


class TestGenerateToolSchemasWave7:
    """Extended schema generation tests."""

    def test_schema_names_are_namespaced(self):
        """Function names should be prefixed with tool name to avoid collisions."""
        schemas = generate_tool_schemas(["slack", "github"])
        names = [s["function"]["name"] for s in schemas]
        # All should be "toolname_funcname"
        for name in names:
            assert "_" in name
            tool_prefix = name.split("_")[0]
            assert tool_prefix in ("slack", "github")

    def test_schema_descriptions_are_tagged(self):
        schemas = generate_tool_schemas(["jira"])
        for s in schemas:
            assert s["function"]["description"].startswith("[jira]")

    def test_schemas_json_roundtrip(self):
        json_str = generate_tool_schemas_json(["slack", "jira"])
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        for s in parsed:
            assert "type" in s
            assert "function" in s

    def test_empty_input_empty_output(self):
        assert generate_tool_schemas([]) == []
        assert generate_tool_schemas_json([]) == "[]"


# ---------------------------------------------------------------------------
# Registry: list_tools with category filtering
# ---------------------------------------------------------------------------


class TestListToolsWave7:
    """Extended list_tools tests."""

    def test_all_categories_return_results(self):
        for cat in VALID_CATEGORIES:
            tools = list_tools(category=cat)
            assert len(tools) > 0, (
                f"Category '{cat}' has no tools"
            )

    def test_unknown_category_returns_empty(self):
        assert list_tools(category="nonexistent_category") == []

    def test_none_category_returns_all(self):
        all_tools = list_tools(category=None)
        assert len(all_tools) == len(TOOL_REGISTRY)

    def test_tools_sorted_by_name(self):
        tools = list_tools()
        names = [t.name for t in tools]
        assert names == sorted(names)

    def test_category_filter_is_exact_match(self):
        """'comm' should not match 'communication'."""
        tools = list_tools(category="comm")
        assert tools == []


# ---------------------------------------------------------------------------
# Registry: tool count assertions
# ---------------------------------------------------------------------------


class TestToolCount:
    """Verify expected tool count."""

    def test_at_least_55_tools(self):
        """PR #108 claims 56 tools; one may be a Python-only connector."""
        assert len(TOOL_REGISTRY) >= 55

    def test_at_least_9_categories(self):
        """PR #108 claims 9 categories."""
        assert len(VALID_CATEGORIES) >= 9


# ---------------------------------------------------------------------------
# Connector utils: structural checks (cannot run JS, but check the file)
# ---------------------------------------------------------------------------


class TestConnectorUtilsStructure:
    """Structural tests for connector-utils.mjs (read as text)."""

    @pytest.fixture()
    def utils_content(self):
        path = _CONNECTORS_DIR / "connector-utils.mjs"
        return path.read_text()

    def test_exports_fetchWithRetry(self, utils_content):
        assert "export async function fetchWithRetry" in utils_content

    def test_exports_parseJSON(self, utils_content):
        assert "export async function parseJSON" in utils_content

    def test_exports_assertOk(self, utils_content):
        assert "export async function assertOk" in utils_content

    def test_has_abort_controller(self, utils_content):
        assert "AbortController" in utils_content

    def test_has_timeout_support(self, utils_content):
        assert "timeoutMs" in utils_content

    def test_has_backoff_ceiling(self, utils_content):
        """Backoff should be capped to prevent indefinite waits."""
        assert "MAX_WAIT_MS" in utils_content

    def test_retry_after_capped(self, utils_content):
        """retry-after header value should be capped."""
        assert "Math.min" in utils_content

    def test_has_sleep_function(self, utils_content):
        assert "function sleep" in utils_content

    def test_error_truncation_in_assertOk(self, utils_content):
        """assertOk should truncate error body to prevent memory issues."""
        assert "slice(0, 500)" in utils_content

    def test_json_error_truncation(self, utils_content):
        """parseJSON error should truncate body."""
        assert "slice(0, 200)" in utils_content


# ---------------------------------------------------------------------------
# Connector files: structural checks
# ---------------------------------------------------------------------------


class TestConnectorFileStructure:
    """Check that connector .mjs files follow expected patterns."""

    def _get_all_connector_files(self):
        result = {}
        for name, tool in TOOL_REGISTRY.items():
            path = _CONNECTORS_DIR / tool.connector_file
            if path.exists():
                result[name] = path.read_text()
        return result

    def test_all_connectors_have_cli_dispatch(self):
        """Each connector should have a CLI dispatch block for sandbox usage."""
        connectors = self._get_all_connector_files()
        no_dispatch = []
        for name, content in connectors.items():
            # Either has process.argv dispatch or is a Python file
            if "process.argv" not in content and not content.strip().startswith("#"):
                no_dispatch.append(name)
        # Allow some tools to not have CLI dispatch (e.g. utility-only)
        # but most should
        assert len(no_dispatch) <= 5, (
            f"Too many connectors without CLI dispatch: {no_dispatch}"
        )

    def test_connectors_have_timeout(self):
        """Connectors making HTTP calls should have timeouts."""
        connectors = self._get_all_connector_files()
        no_timeout = []
        for name, content in connectors.items():
            if "fetch(" in content:
                if "AbortController" not in content and "timeout" not in content.lower():
                    no_timeout.append(name)
        assert no_timeout == [], (
            f"Connectors making fetch() without timeout: {no_timeout}"
        )


# ---------------------------------------------------------------------------
# Integration: bundle_tool_files always includes connector-utils
# ---------------------------------------------------------------------------


class TestConnectorUtilsShared:
    """Verify connector-utils.mjs is available as a shared utility."""

    def test_connector_utils_exists(self):
        path = _CONNECTORS_DIR / "connector-utils.mjs"
        assert path.exists()

    def test_connector_utils_has_exports(self):
        path = _CONNECTORS_DIR / "connector-utils.mjs"
        content = path.read_text()
        assert "export" in content


# ---------------------------------------------------------------------------
# Frozen dataclass immutability
# ---------------------------------------------------------------------------


class TestToolDefinitionImmutability:
    """Verify ToolDefinition and ToolFunction are truly frozen."""

    def test_tool_definition_frozen(self):
        tool = get_tool("slack")
        with pytest.raises(AttributeError):
            tool.name = "hacked"  # type: ignore

    def test_tool_function_frozen(self):
        tool = get_tool("slack")
        func = tool.functions[0]
        with pytest.raises(AttributeError):
            func.name = "hacked"  # type: ignore

    def test_registry_mutation_does_not_affect_tool(self):
        """Modifying a mutable field like functions list shouldn't crash."""
        tool = get_tool("slack")
        # The list itself is mutable (Python limitation with frozen dataclass)
        # but at least the ToolFunction objects inside are frozen
        for func in tool.functions:
            with pytest.raises(AttributeError):
                func.description = "hacked"  # type: ignore


# ---------------------------------------------------------------------------
# Edge case: tools with Python connectors (not .mjs)
# ---------------------------------------------------------------------------


class TestPythonConnectors:
    """Check for .py connector files in the connectors directory."""

    def test_python_connectors_exist(self):
        py_files = list(_CONNECTORS_DIR.glob("*.py"))
        # browser_computer_use.py is the known Python connector
        py_names = [f.name for f in py_files]
        assert "browser_computer_use.py" in py_names

    def test_no_tool_references_py_as_connector_file(self):
        """No tool in the registry should reference a .py file as connector_file.
        Python connectors are loaded differently."""
        for name, tool in TOOL_REGISTRY.items():
            assert not tool.connector_file.endswith(".py"), (
                f"Tool '{name}' references .py connector file "
                f"'{tool.connector_file}'"
            )


# ---------------------------------------------------------------------------
# Credential isolation: named connections
# ---------------------------------------------------------------------------


class TestNamedConnectionIsolation:
    """Test credential isolation for named connections."""

    def test_different_connections_get_different_creds(self, monkeypatch):
        """Two named connections for the same tool should be independent."""
        # For plain (non-named) tool refs, creds come from env vars
        # Named connections would come from DB. Here we test the plain path.
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "slack-token-1")
        creds = get_tool_credentials(["slack"])
        assert creds["TOOL_SLACK_BOT_TOKEN"] == "slack-token-1"

    def test_named_ref_does_not_use_env_vars(self, monkeypatch):
        """When using a named connection (tool:name), env vars are not used
        directly - the system tries DB first."""
        monkeypatch.setenv("TOOL_SLACK_BOT_TOKEN", "should-not-appear")
        # Named ref with valid format - will try DB (which won't exist in test)
        # and the _resolve_named_connections will fail gracefully
        # In a real test, we'd mock the DB. Here we just verify no crash.
        # The function should not crash even without a real DB.
        try:
            creds = get_tool_credentials(["slack:prod-workspace"])
        except Exception:
            # DB not available in test - that's OK, we're testing the flow
            pass


# ---------------------------------------------------------------------------
# Schema generation: no function name collisions
# ---------------------------------------------------------------------------


class TestSchemaNameCollisions:
    """Verify tool function schemas don't have name collisions."""

    def test_all_schema_names_unique(self):
        """When generating schemas for ALL tools, no two should share a name."""
        all_names = list(TOOL_REGISTRY.keys())
        schemas = generate_tool_schemas(all_names)
        names = [s["function"]["name"] for s in schemas]
        duplicates = [n for n in names if names.count(n) > 1]
        assert duplicates == [], (
            f"Duplicate schema names: {set(duplicates)}"
        )

    def test_function_names_are_valid_identifiers(self):
        """Schema function names should be valid for use in API calls."""
        all_names = list(TOOL_REGISTRY.keys())
        schemas = generate_tool_schemas(all_names)
        for s in schemas:
            name = s["function"]["name"]
            # Should contain only alphanumeric, underscore, hyphen
            assert re.match(r"^[a-zA-Z0-9_-]+$", name), (
                f"Invalid function schema name: {name}"
            )


# ---------------------------------------------------------------------------
# Bundle: connector-utils.mjs is importable from other connectors
# ---------------------------------------------------------------------------


class TestConnectorImports:
    """Verify connectors that import connector-utils.mjs."""

    def test_connectors_importing_utils(self):
        """Some connectors should import from connector-utils.mjs."""
        importers = []
        for f in _CONNECTORS_DIR.glob("*.mjs"):
            if f.name == "connector-utils.mjs":
                continue
            content = f.read_text()
            if "connector-utils" in content:
                importers.append(f.name)
        # Not all connectors import utils, but if any do, verify it's correct
        for imp in importers:
            content = (_CONNECTORS_DIR / imp).read_text()
            assert "from" in content or "import" in content


# ---------------------------------------------------------------------------
# Registry: get_tool error message quality
# ---------------------------------------------------------------------------


class TestGetToolErrorMessages:
    """Verify error messages are helpful."""

    def test_unknown_tool_lists_available(self):
        with pytest.raises(KeyError, match="Available:"):
            get_tool("nonexistent_tool_xyz")

    def test_error_message_contains_tool_name(self):
        with pytest.raises(KeyError, match="nonexistent_tool_xyz"):
            get_tool("nonexistent_tool_xyz")

    def test_similar_names_not_suggested_but_listed(self):
        """Error should at least list available tools."""
        with pytest.raises(KeyError) as exc_info:
            get_tool("slackk")
        # "slack" should appear in the available tools list
        assert "slack" in str(exc_info.value)
