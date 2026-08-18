"""Deep tests for new connectors (langfuse, qdrant, gcs, azure-blob, exa)
and registry integrity.

Tests cover:
 1. Registry completeness - every tool .mjs has a registry entry (no orphans)
 2. Reverse check - every registry entry's connector_file exists on disk
 3. Function signature audit - try/catch, AbortController, structured response
 4. Credential coverage - every credential env var is in _SENSITIVE_KEYS
 5. No hardcoded URLs - scan for hardcoded API URLs that should be configurable
 6. Category integrity - all 5 new connectors have valid categories
 7. Connector JS syntax - node --check on each .mjs file
 8. Description quality - non-empty descriptions under 200 chars
 9. Duplicate detection - no two tools share name or connector_file
10. Total connector count - registry has 62 entries (58 + 5 new - 1 util = 62)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
CONNECTORS_DIR = REPO_ROOT / "src" / "sandcastle" / "engine" / "tools" / "connectors"
ROUTES_PY = REPO_ROOT / "src" / "sandcastle" / "api" / "routes.py"

# Add repo to sys.path so we can import registry
sys.path.insert(0, str(REPO_ROOT / "src"))

from sandcastle.engine.tools.registry import TOOL_REGISTRY, VALID_CATEGORIES  # noqa: E402

# The 5 new connectors added in v0.22
NEW_CONNECTORS = ["langfuse", "qdrant", "gcs", "azure-blob", "exa"]

# connector-utils.mjs is a shared utility, NOT a registered tool
UTILITY_FILES = {"connector-utils.mjs"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_connector_mjs_files() -> set[str]:
    """Return all .mjs files in connectors/ that are actual connectors (not utilities)."""
    all_mjs = {f.name for f in CONNECTORS_DIR.glob("*.mjs")}
    return all_mjs - UTILITY_FILES


def get_sensitive_keys() -> frozenset[str]:
    """Parse _SENSITIVE_KEYS from routes.py."""
    text = ROUTES_PY.read_text(encoding="utf-8")
    match = re.search(r"_SENSITIVE_KEYS\s*=\s*frozenset\(\{([^}]+)\}", text, re.DOTALL)
    assert match, "_SENSITIVE_KEYS block not found in routes.py"
    block = match.group(1)
    keys = set(re.findall(r'"([^"]+)"', block))
    return frozenset(keys)


def read_connector(name: str) -> str:
    """Read a connector file by its connector_file attribute from the registry."""
    tool = TOOL_REGISTRY[name]
    path = CONNECTORS_DIR / tool.connector_file
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Registry completeness - every tool .mjs has a matching registry entry
# ---------------------------------------------------------------------------

class TestRegistryCompleteness:
    def test_no_orphan_mjs_files(self):
        """Every .mjs connector file (excluding utilities) must be in TOOL_REGISTRY."""
        mjs_files = get_connector_mjs_files()
        registry_files = {t.connector_file for t in TOOL_REGISTRY.values()}
        orphans = mjs_files - registry_files
        assert not orphans, (
            f"Orphaned .mjs files (on disk but not in TOOL_REGISTRY): {sorted(orphans)}"
        )

    def test_new_connectors_all_present(self):
        """All 5 new connectors must exist in TOOL_REGISTRY."""
        missing = [c for c in NEW_CONNECTORS if c not in TOOL_REGISTRY]
        assert not missing, f"New connectors missing from TOOL_REGISTRY: {missing}"


# ---------------------------------------------------------------------------
# 2. Reverse check - every registry entry has its file on disk
# ---------------------------------------------------------------------------

class TestReverseCheck:
    def test_all_registry_files_exist_on_disk(self):
        """Every connector_file in TOOL_REGISTRY must exist in connectors/."""
        missing = []
        for name, tool in TOOL_REGISTRY.items():
            path = CONNECTORS_DIR / tool.connector_file
            if not path.exists():
                missing.append(f"{name} -> {tool.connector_file}")
        assert not missing, (
            f"Registry entries whose connector_file is missing on disk:\n"
            + "\n".join(missing)
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_new_connector_file_exists(self, connector: str):
        """Each new connector's .mjs file must exist."""
        tool = TOOL_REGISTRY[connector]
        path = CONNECTORS_DIR / tool.connector_file
        assert path.exists(), f"{connector}: file not found at {path}"


# ---------------------------------------------------------------------------
# 3. Function signature audit for the 5 new connectors
# ---------------------------------------------------------------------------

class TestFunctionSignatureAudit:
    """Verify safety patterns in every exported function of the 5 new connectors."""

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_has_try_catch_or_finally(self, connector: str):
        """Connector must use try/catch or try/finally for error handling."""
        content = read_connector(connector)
        has_try = "try {" in content or "try{" in content
        has_catch_or_finally = "} catch" in content or "} finally" in content
        assert has_try and has_catch_or_finally, (
            f"{connector}: missing try/catch or try/finally error handling"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_uses_abort_controller(self, connector: str):
        """Connector must use AbortController for timeout control."""
        content = read_connector(connector)
        assert "AbortController" in content, (
            f"{connector}: no AbortController found - timeout handling missing"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_returns_structured_response(self, connector: str):
        """Exported functions must return structured objects, not raw text."""
        content = read_connector(connector)
        # All exported functions should return an object literal, not plain strings
        exports = re.findall(r"export async function (\w+)", content)
        assert len(exports) >= 1, f"{connector}: no exported async functions found"
        # Each return statement should return an object or call a function returning one
        # Check that we have at least one return { pattern (structured response)
        assert re.search(r"return\s*\{", content), (
            f"{connector}: no structured return object found (return {{...}})"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_exported_functions_documented(self, connector: str):
        """All exported functions must be present in CLI dispatch table."""
        content = read_connector(connector)
        # Extract exported function names
        exported = set(re.findall(r"export async function (\w+)", content))
        # Extract dispatch table keys (e.g. { search, upsert, get_collections })
        dispatch_match = re.search(r"const dispatch\s*=\s*\{([^}]+)\}", content)
        assert dispatch_match, f"{connector}: no CLI dispatch table found"
        dispatch_body = dispatch_match.group(1)
        dispatch_fns = set(re.findall(r"\b(\w+)\b", dispatch_body))
        missing_from_dispatch = exported - dispatch_fns
        assert not missing_from_dispatch, (
            f"{connector}: exported functions not in dispatch table: {missing_from_dispatch}"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_timeout_constant_defined(self, connector: str):
        """Each connector must define a TIMEOUT constant."""
        content = read_connector(connector)
        assert re.search(r"const TIMEOUT\s*=", content), (
            f"{connector}: no TIMEOUT constant found"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_abort_signal_passed_to_fetch(self, connector: str):
        """AbortController signal must be passed to fetch calls."""
        content = read_connector(connector)
        # signal should appear in fetch options
        assert re.search(r"signal[,\s\}]", content), (
            f"{connector}: AbortController signal not passed to fetch"
        )


# ---------------------------------------------------------------------------
# 4. Credential coverage - every credential env var is in _SENSITIVE_KEYS
# ---------------------------------------------------------------------------

class TestCredentialCoverage:
    # Explicit list of secret-type credential env vars for the 5 new connectors.
    # Non-secret config vars like TOOL_GCS_PROJECT_ID (public identifier) and
    # TOOL_LANGFUSE_HOST (endpoint URL, not auth) are intentionally excluded.
    NEW_CONNECTOR_SECRET_VARS = {
        "langfuse": ["TOOL_LANGFUSE_SECRET_KEY", "TOOL_LANGFUSE_PUBLIC_KEY"],
        "qdrant": ["TOOL_QDRANT_URL", "TOOL_QDRANT_API_KEY"],
        "gcs": ["TOOL_GCS_SERVICE_ACCOUNT_JSON"],
        "azure-blob": ["TOOL_AZURE_STORAGE_CONNECTION_STRING", "TOOL_AZURE_STORAGE_KEY"],
        "exa": ["TOOL_EXA_API_KEY"],
    }

    def test_new_connector_credentials_in_sensitive_keys(self):
        """Explicitly secret credential vars for the 5 new connectors must be in _SENSITIVE_KEYS.

        Non-secret config vars (TOOL_GCS_PROJECT_ID, TOOL_LANGFUSE_HOST,
        TOOL_QDRANT_API_KEY when used alone) are not required to be in _SENSITIVE_KEYS.
        """
        sensitive = get_sensitive_keys()
        failures = []
        for connector, secret_vars in self.NEW_CONNECTOR_SECRET_VARS.items():
            for env_var in secret_vars:
                lower = env_var.lower()
                if lower not in sensitive:
                    failures.append(f"{connector}: {env_var} not in _SENSITIVE_KEYS")
        assert not failures, "\n".join(failures)

    def test_all_registry_credentials_in_sensitive_keys(self):
        """All TOOL_* credential vars that contain secrets must be in _SENSITIVE_KEYS.

        Detects secret-bearing vars by naming pattern: key, token, secret, password, json, dsn.
        Non-secret config vars (host, port, user, project_id, base_url) are excluded.
        URL vars are included because they can embed credentials.
        """
        sensitive = get_sensitive_keys()
        # Env vars explicitly known to be non-secret config (not auth tokens/keys)
        non_secret_vars = {
            "tool_smtp_host", "tool_smtp_port", "tool_smtp_user",
            "tool_jira_base_url", "tool_jira_email",
            "tool_gcs_project_id",
            "tool_langfuse_host",
            # Salesforce instance URL is a public org subdomain, not an auth secret
            "tool_salesforce_instance_url",
        }
        failures = []
        for name, tool in TOOL_REGISTRY.items():
            for env_var in tool.credential_env_vars:
                lower = env_var.lower()
                if lower in non_secret_vars:
                    continue
                # Only flag vars that look like secrets by naming pattern
                secret_indicators = ["key", "token", "secret", "password", "json", "_url"]
                is_likely_secret = any(ind in lower for ind in secret_indicators)
                if is_likely_secret and lower not in sensitive:
                    failures.append(f"{name}: {env_var} not in _SENSITIVE_KEYS")
        assert not failures, (
            "Credential env vars missing from _SENSITIVE_KEYS:\n" + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# 5. No hardcoded URLs that should be configurable
# ---------------------------------------------------------------------------

class TestNoHardcodedUrls:
    # Known stable well-known endpoints that are acceptable to hardcode
    ACCEPTABLE_URLS = {
        "https://api.exa.ai",
        "https://storage.googleapis.com",
        "https://oauth2.googleapis.com",
        "https://www.googleapis.com",
        "https://cloud.langfuse.com",  # default fallback, overridable via env
        ".blob.core.windows.net",       # pattern, not full URL
    }

    def _find_hardcoded_urls(self, content: str, acceptable: set[str]) -> list[str]:
        """Find https:// URLs that are not in the acceptable set."""
        # Match string literals containing https://
        found = re.findall(r'"(https://[^"]+)"', content)
        found += re.findall(r"'(https://[^']+)'", content)
        # Filter out acceptable known-stable endpoints
        suspicious = []
        for url in found:
            if any(acc in url for acc in acceptable):
                continue
            # Skip if it's just a template/example string in a comment or description
            suspicious.append(url)
        return suspicious

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_no_unexpected_hardcoded_urls(self, connector: str):
        """Connectors should not have hardcoded API URLs (except well-known stable ones)."""
        content = read_connector(connector)
        # Remove comments
        content_no_comments = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", content)
        suspicious = self._find_hardcoded_urls(content_no_comments, self.ACCEPTABLE_URLS)
        assert not suspicious, (
            f"{connector}: hardcoded URLs that should be configurable: {suspicious}"
        )

    def test_langfuse_host_is_overridable(self):
        """Langfuse host must be readable from env var (not purely hardcoded)."""
        content = (CONNECTORS_DIR / "langfuse.mjs").read_text()
        assert "process.env.TOOL_LANGFUSE_HOST" in content, (
            "langfuse: host must be configurable via TOOL_LANGFUSE_HOST"
        )

    def test_qdrant_url_is_overridable(self):
        """Qdrant base URL must be readable from env var."""
        content = (CONNECTORS_DIR / "qdrant.mjs").read_text()
        assert "process.env.TOOL_QDRANT_URL" in content, (
            "qdrant: URL must be configurable via TOOL_QDRANT_URL"
        )


# ---------------------------------------------------------------------------
# 6. Category integrity
# ---------------------------------------------------------------------------

class TestCategoryIntegrity:
    def test_new_connectors_have_valid_categories(self):
        """All 5 new connectors must use categories that exist in VALID_CATEGORIES."""
        failures = []
        for connector in NEW_CONNECTORS:
            tool = TOOL_REGISTRY[connector]
            if tool.category not in VALID_CATEGORIES:
                failures.append(
                    f"{connector}: category '{tool.category}' not in VALID_CATEGORIES "
                    f"{sorted(VALID_CATEGORIES)}"
                )
        assert not failures, "\n".join(failures)

    def test_langfuse_category_is_observability(self):
        assert TOOL_REGISTRY["langfuse"].category == "observability"

    def test_qdrant_category_is_data(self):
        assert TOOL_REGISTRY["qdrant"].category == "data"

    def test_gcs_category_is_storage(self):
        assert TOOL_REGISTRY["gcs"].category == "storage"

    def test_azure_blob_category_is_storage(self):
        assert TOOL_REGISTRY["azure-blob"].category == "storage"

    def test_exa_category_is_ai(self):
        assert TOOL_REGISTRY["exa"].category == "ai"

    def test_all_tools_have_valid_category(self):
        """Every tool in the registry must have a non-empty valid category."""
        failures = []
        for name, tool in TOOL_REGISTRY.items():
            if not tool.category:
                failures.append(f"{name}: empty category")
            elif tool.category not in VALID_CATEGORIES:
                failures.append(f"{name}: unknown category '{tool.category}'")
        assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# 7. Connector JS syntax check via node --check
# ---------------------------------------------------------------------------

class TestConnectorJsSyntax:
    def _node_check(self, filename: str) -> tuple[bool, str]:
        """Run node --check on a .mjs file. Returns (ok, stderr)."""
        path = CONNECTORS_DIR / filename
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0, result.stderr.strip()

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_js_syntax_valid(self, connector: str):
        """Each new connector's .mjs must pass node --check (valid JavaScript)."""
        tool = TOOL_REGISTRY[connector]
        ok, stderr = self._node_check(tool.connector_file)
        assert ok, f"{connector} ({tool.connector_file}): JS syntax error:\n{stderr}"

    def test_connector_utils_syntax_valid(self):
        """connector-utils.mjs must also pass node --check."""
        ok, stderr = self._node_check("connector-utils.mjs")
        assert ok, f"connector-utils.mjs: JS syntax error:\n{stderr}"


# ---------------------------------------------------------------------------
# 8. Description quality
# ---------------------------------------------------------------------------

class TestDescriptionQuality:
    MAX_DESC_LEN = 200

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_tool_description_non_empty(self, connector: str):
        tool = TOOL_REGISTRY[connector]
        assert tool.description.strip(), f"{connector}: empty tool description"

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_tool_description_under_200_chars(self, connector: str):
        tool = TOOL_REGISTRY[connector]
        assert len(tool.description) <= self.MAX_DESC_LEN, (
            f"{connector}: description too long ({len(tool.description)} chars): "
            f"{tool.description!r}"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_all_functions_have_descriptions(self, connector: str):
        tool = TOOL_REGISTRY[connector]
        for fn in tool.functions:
            assert fn.description.strip(), (
                f"{connector}.{fn.name}: empty function description"
            )
            assert len(fn.description) <= self.MAX_DESC_LEN, (
                f"{connector}.{fn.name}: function description too long "
                f"({len(fn.description)} chars)"
            )

    def test_all_registry_tool_descriptions_non_empty(self):
        """Every tool in registry must have a non-empty description."""
        failures = [
            name for name, t in TOOL_REGISTRY.items() if not t.description.strip()
        ]
        assert not failures, f"Tools with empty descriptions: {failures}"

    def test_all_registry_function_descriptions_non_empty(self):
        """Every tool function must have a non-empty description."""
        failures = []
        for name, tool in TOOL_REGISTRY.items():
            for fn in tool.functions:
                if not fn.description.strip():
                    failures.append(f"{name}.{fn.name}")
        assert not failures, f"Functions with empty descriptions: {failures}"


# ---------------------------------------------------------------------------
# 9. Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_no_duplicate_tool_names(self):
        """TOOL_REGISTRY keys (tool names) must be unique by definition (dict)."""
        # The dict itself enforces this, but let's also verify name attribute matches key
        mismatches = [
            f"{key}: name='{tool.name}'"
            for key, tool in TOOL_REGISTRY.items()
            if key != tool.name
        ]
        assert not mismatches, (
            "Registry key does not match tool.name attribute:\n" + "\n".join(mismatches)
        )

    def test_no_duplicate_connector_files(self):
        """No two registry entries should share the same connector_file."""
        seen: dict[str, str] = {}
        duplicates = []
        for name, tool in TOOL_REGISTRY.items():
            if tool.connector_file in seen:
                duplicates.append(
                    f"{tool.connector_file} shared by '{seen[tool.connector_file]}' and '{name}'"
                )
            else:
                seen[tool.connector_file] = name
        assert not duplicates, "Duplicate connector_file entries:\n" + "\n".join(duplicates)

    def test_no_duplicate_function_names_within_tool(self):
        """Each tool must not have two functions with the same name."""
        failures = []
        for name, tool in TOOL_REGISTRY.items():
            fn_names = [fn.name for fn in tool.functions]
            seen = set()
            for fn_name in fn_names:
                if fn_name in seen:
                    failures.append(f"{name}: duplicate function '{fn_name}'")
                seen.add(fn_name)
        assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# 10. Total connector count
# ---------------------------------------------------------------------------

class TestTotalConnectorCount:
    EXPECTED_REGISTRY_COUNT = 64  # 63 + websearch (keyless free web search)
    EXPECTED_MJS_ON_DISK = 65    # 64 + websearch.mjs

    def test_registry_has_expected_count(self):
        """TOOL_REGISTRY must have exactly 64 entries."""
        count = len(TOOL_REGISTRY)
        assert count == self.EXPECTED_REGISTRY_COUNT, (
            f"Expected {self.EXPECTED_REGISTRY_COUNT} registry entries, got {count}. "
            f"Tools: {sorted(TOOL_REGISTRY.keys())}"
        )

    def test_mjs_files_on_disk_count(self):
        """There must be exactly 65 .mjs files in connectors/ (64 tools + 1 utility)."""
        mjs_files = list(CONNECTORS_DIR.glob("*.mjs"))
        count = len(mjs_files)
        assert count == self.EXPECTED_MJS_ON_DISK, (
            f"Expected {self.EXPECTED_MJS_ON_DISK} .mjs files, found {count}. "
            f"Files: {sorted(f.name for f in mjs_files)}"
        )

    def test_new_connectors_count(self):
        """Exactly 5 new connectors must be present."""
        present = [c for c in NEW_CONNECTORS if c in TOOL_REGISTRY]
        assert len(present) == 5, (
            f"Expected 5 new connectors, found {len(present)}: {present}"
        )


# ---------------------------------------------------------------------------
# 11. Bonus: Structural integrity of new connector registry entries
# ---------------------------------------------------------------------------

class TestNewConnectorRegistryEntries:
    """Detailed structural checks for each of the 5 new connector registry entries."""

    def test_langfuse_functions(self):
        tool = TOOL_REGISTRY["langfuse"]
        fn_names = {fn.name for fn in tool.functions}
        assert "create_trace" in fn_names
        assert "create_generation" in fn_names
        assert "create_score" in fn_names
        assert "get_trace" in fn_names

    def test_langfuse_credentials(self):
        tool = TOOL_REGISTRY["langfuse"]
        assert "TOOL_LANGFUSE_SECRET_KEY" in tool.credential_env_vars
        assert "TOOL_LANGFUSE_PUBLIC_KEY" in tool.credential_env_vars

    def test_langfuse_connector_file(self):
        assert TOOL_REGISTRY["langfuse"].connector_file == "langfuse.mjs"

    def test_qdrant_functions(self):
        tool = TOOL_REGISTRY["qdrant"]
        fn_names = {fn.name for fn in tool.functions}
        assert "search" in fn_names
        assert "upsert" in fn_names
        assert "get_collections" in fn_names
        assert "delete_points" in fn_names

    def test_qdrant_credentials(self):
        tool = TOOL_REGISTRY["qdrant"]
        assert "TOOL_QDRANT_URL" in tool.credential_env_vars

    def test_qdrant_connector_file(self):
        assert TOOL_REGISTRY["qdrant"].connector_file == "qdrant.mjs"

    def test_gcs_functions(self):
        tool = TOOL_REGISTRY["gcs"]
        fn_names = {fn.name for fn in tool.functions}
        assert "upload" in fn_names
        assert "download" in fn_names
        assert "list_objects" in fn_names
        assert "delete_object" in fn_names

    def test_gcs_credentials(self):
        tool = TOOL_REGISTRY["gcs"]
        assert "TOOL_GCS_SERVICE_ACCOUNT_JSON" in tool.credential_env_vars

    def test_gcs_connector_file(self):
        assert TOOL_REGISTRY["gcs"].connector_file == "gcs.mjs"

    def test_azure_blob_functions(self):
        tool = TOOL_REGISTRY["azure-blob"]
        fn_names = {fn.name for fn in tool.functions}
        assert "upload" in fn_names
        assert "download" in fn_names
        assert "list_blobs" in fn_names
        assert "delete_blob" in fn_names

    def test_azure_blob_credentials(self):
        tool = TOOL_REGISTRY["azure-blob"]
        assert "TOOL_AZURE_STORAGE_CONNECTION_STRING" in tool.credential_env_vars
        assert [
            "TOOL_AZURE_STORAGE_ACCOUNT",
            "TOOL_AZURE_STORAGE_KEY",
        ] in tool.alternative_credential_env_vars

    def test_azure_blob_connector_file(self):
        assert TOOL_REGISTRY["azure-blob"].connector_file == "azure-blob.mjs"

    def test_exa_functions(self):
        tool = TOOL_REGISTRY["exa"]
        fn_names = {fn.name for fn in tool.functions}
        assert "search" in fn_names
        assert "get_contents" in fn_names
        assert "find_similar" in fn_names

    def test_exa_credentials(self):
        tool = TOOL_REGISTRY["exa"]
        assert "TOOL_EXA_API_KEY" in tool.credential_env_vars

    def test_exa_connector_file(self):
        assert TOOL_REGISTRY["exa"].connector_file == "exa.mjs"

    def test_all_new_connectors_have_functions(self):
        """Each new connector must have at least 1 registered function."""
        for connector in NEW_CONNECTORS:
            tool = TOOL_REGISTRY[connector]
            assert len(tool.functions) >= 1, (
                f"{connector}: has no registered functions"
            )

    def test_all_new_connectors_have_credentials(self):
        """Each new connector must declare at least 1 credential env var."""
        for connector in NEW_CONNECTORS:
            tool = TOOL_REGISTRY[connector]
            assert len(tool.credential_env_vars) >= 1, (
                f"{connector}: has no credential_env_vars"
            )

    def test_all_new_connectors_have_icon(self):
        """Each new connector should have a non-empty icon identifier."""
        for connector in NEW_CONNECTORS:
            tool = TOOL_REGISTRY[connector]
            assert tool.icon, f"{connector}: missing icon identifier"


# ---------------------------------------------------------------------------
# 12. Content-level JS audit for the 5 new connectors
# ---------------------------------------------------------------------------

class TestConnectorContentAudit:
    """Deeper content-level checks on the actual .mjs source of each new connector."""

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_uses_process_env_for_credentials(self, connector: str):
        """Credentials must be sourced from process.env, not hardcoded."""
        content = read_connector(connector)
        tool = TOOL_REGISTRY[connector]
        for env_var in tool.credential_env_vars:
            assert f'process.env.{env_var}' in content, (
                f"{connector}: credential {env_var} not read from process.env"
            )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_has_cli_dispatch(self, connector: str):
        """Each connector must have a CLI dispatch block for direct invocation."""
        content = read_connector(connector)
        connector_file = TOOL_REGISTRY[connector].connector_file
        stem = connector_file.replace(".mjs", "")
        # Dispatch pattern: if (process.argv[1]?.endsWith("<stem>.mjs"))
        assert f'endsWith("{stem}.mjs")' in content, (
            f"{connector}: no CLI dispatch block found"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_error_response_truncated(self, connector: str):
        """Error messages from API responses must be truncated to prevent log flooding."""
        content = read_connector(connector)
        # All 5 connectors should truncate error text with .slice(0, N)
        assert ".slice(0," in content, (
            f"{connector}: API error text not truncated with .slice(0, N) - "
            "may cause log flooding on verbose errors"
        )

    @pytest.mark.parametrize("connector", NEW_CONNECTORS)
    def test_has_clearTimeout_cleanup(self, connector: str):
        """AbortController timer must be cleaned up with clearTimeout."""
        content = read_connector(connector)
        assert "clearTimeout" in content, (
            f"{connector}: no clearTimeout call found - AbortController timer may leak"
        )

    def test_langfuse_uses_basic_auth(self):
        """Langfuse connector must use Basic auth (public_key:secret_key)."""
        content = read_connector("langfuse")
        assert "Basic " in content or "Basic auth" in content or "base64" in content, (
            "langfuse: Basic auth not found - may use wrong auth method"
        )

    def test_qdrant_optional_api_key(self):
        """Qdrant connector must handle optional API key gracefully."""
        content = read_connector("qdrant")
        # API key is optional for self-hosted instances
        assert 'TOOL_QDRANT_API_KEY' in content
        assert 'if (API_KEY)' in content or 'API_KEY &&' in content or 'if(API_KEY)' in content, (
            "qdrant: API key should be conditionally applied (it's optional for self-hosted)"
        )

    def test_gcs_uses_jwt_signing(self):
        """GCS connector must use JWT signing for service account auth."""
        content = read_connector("gcs")
        assert "RSA-SHA256" in content or "createSign" in content, (
            "gcs: JWT signing not found - service account auth may be broken"
        )

    def test_azure_blob_uses_shared_key_auth(self):
        """Azure Blob connector must use SharedKey authorization."""
        content = read_connector("azure-blob")
        assert "SharedKey" in content, (
            "azure-blob: SharedKey auth not found"
        )

    def test_azure_blob_supports_connection_string(self):
        """Azure Blob connector must support parsing connection strings."""
        content = read_connector("azure-blob")
        assert "parseConnectionString" in content or "CONNECTION_STRING" in content, (
            "azure-blob: connection string parsing not found"
        )

    def test_exa_uses_x_api_key_header(self):
        """Exa connector must send the API key as x-api-key header."""
        content = read_connector("exa")
        assert '"x-api-key"' in content or "'x-api-key'" in content, (
            "exa: x-api-key header not found - authentication may be broken"
        )
