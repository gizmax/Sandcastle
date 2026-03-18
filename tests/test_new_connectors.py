"""Tests for the v0.23 ecosystem integration connectors.

Covers: registry registration, required credentials, .mjs files on disk,
and sensitive key presence in routes._SENSITIVE_KEYS.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONNECTORS_DIR = Path(__file__).parent.parent / "src" / "sandcastle" / "engine" / "tools" / "connectors"

NEW_CONNECTORS = [
    "langfuse",
    "qdrant",
    "gcs",
    "azure-blob",
    "exa",
]


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistryRegistration:
    """Each new connector must be present in TOOL_REGISTRY."""

    def setup_method(self):
        from sandcastle.engine.tools.registry import TOOL_REGISTRY
        self.registry = TOOL_REGISTRY

    def test_langfuse_registered(self):
        assert "langfuse" in self.registry

    def test_qdrant_registered(self):
        assert "qdrant" in self.registry

    def test_gcs_registered(self):
        assert "gcs" in self.registry

    def test_azure_blob_registered(self):
        assert "azure-blob" in self.registry

    def test_exa_registered(self):
        assert "exa" in self.registry

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_all_new_connectors_registered(self, name):
        assert name in self.registry, f"'{name}' not found in TOOL_REGISTRY"


class TestRegistryMetadata:
    """Each new connector must have correct category, non-empty functions, and non-empty credentials."""

    def setup_method(self):
        from sandcastle.engine.tools.registry import TOOL_REGISTRY
        self.registry = TOOL_REGISTRY

    def test_langfuse_category(self):
        assert self.registry["langfuse"].category == "observability"

    def test_qdrant_category(self):
        assert self.registry["qdrant"].category == "data"

    def test_gcs_category(self):
        assert self.registry["gcs"].category == "storage"

    def test_azure_blob_category(self):
        assert self.registry["azure-blob"].category == "storage"

    def test_exa_category(self):
        assert self.registry["exa"].category == "ai"

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_has_functions(self, name):
        tool = self.registry[name]
        assert len(tool.functions) >= 1, f"'{name}' has no functions"

    @pytest.mark.parametrize("name", ["langfuse", "gcs", "azure-blob", "exa"])
    def test_required_credentials_non_empty(self, name):
        tool = self.registry[name]
        assert len(tool.credential_env_vars) >= 1, (
            f"'{name}' has empty required_credentials"
        )

    def test_qdrant_has_url_credential(self):
        # TOOL_QDRANT_URL is required; API key is optional
        creds = self.registry["qdrant"].credential_env_vars
        assert "TOOL_QDRANT_URL" in creds

    def test_langfuse_credentials(self):
        creds = self.registry["langfuse"].credential_env_vars
        assert "TOOL_LANGFUSE_SECRET_KEY" in creds
        assert "TOOL_LANGFUSE_PUBLIC_KEY" in creds

    def test_gcs_credentials(self):
        creds = self.registry["gcs"].credential_env_vars
        assert "TOOL_GCS_SERVICE_ACCOUNT_JSON" in creds
        assert "TOOL_GCS_PROJECT_ID" in creds

    def test_azure_blob_credentials(self):
        creds = self.registry["azure-blob"].credential_env_vars
        assert "TOOL_AZURE_STORAGE_CONNECTION_STRING" in creds

    def test_exa_credentials(self):
        creds = self.registry["exa"].credential_env_vars
        assert "TOOL_EXA_API_KEY" in creds


class TestRegistryFunctions:
    """Each connector must expose the expected function names."""

    def setup_method(self):
        from sandcastle.engine.tools.registry import TOOL_REGISTRY
        self.registry = TOOL_REGISTRY

    def _fn_names(self, tool_name):
        return {f.name for f in self.registry[tool_name].functions}

    def test_langfuse_functions(self):
        names = self._fn_names("langfuse")
        assert "create_trace" in names
        assert "create_generation" in names
        assert "create_score" in names
        assert "get_trace" in names

    def test_qdrant_functions(self):
        names = self._fn_names("qdrant")
        assert "search" in names
        assert "upsert" in names
        assert "get_collections" in names
        assert "delete_points" in names

    def test_gcs_functions(self):
        names = self._fn_names("gcs")
        assert "upload" in names
        assert "download" in names
        assert "list_objects" in names
        assert "delete_object" in names

    def test_azure_blob_functions(self):
        names = self._fn_names("azure-blob")
        assert "upload" in names
        assert "download" in names
        assert "list_blobs" in names
        assert "delete_blob" in names

    def test_exa_functions(self):
        names = self._fn_names("exa")
        assert "search" in names
        assert "get_contents" in names
        assert "find_similar" in names


class TestConnectorFiles:
    """Each new connector .mjs file must exist on disk."""

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_mjs_file_exists(self, name):
        mjs_path = CONNECTORS_DIR / f"{name}.mjs"
        assert mjs_path.exists(), f"Connector file not found: {mjs_path}"

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_mjs_file_not_empty(self, name):
        mjs_path = CONNECTORS_DIR / f"{name}.mjs"
        content = mjs_path.read_text()
        assert len(content) > 100, f"Connector file too small: {mjs_path}"

    def test_langfuse_mjs_has_exports(self):
        content = (CONNECTORS_DIR / "langfuse.mjs").read_text()
        assert "export async function create_trace" in content
        assert "export async function create_generation" in content
        assert "export async function create_score" in content
        assert "export async function get_trace" in content

    def test_qdrant_mjs_has_exports(self):
        content = (CONNECTORS_DIR / "qdrant.mjs").read_text()
        assert "export async function search" in content
        assert "export async function upsert" in content
        assert "export async function get_collections" in content
        assert "export async function delete_points" in content

    def test_gcs_mjs_has_exports(self):
        content = (CONNECTORS_DIR / "gcs.mjs").read_text()
        assert "export async function upload" in content
        assert "export async function download" in content
        assert "export async function list_objects" in content
        assert "export async function delete_object" in content

    def test_azure_blob_mjs_has_exports(self):
        content = (CONNECTORS_DIR / "azure-blob.mjs").read_text()
        assert "export async function upload" in content
        assert "export async function download" in content
        assert "export async function list_blobs" in content
        assert "export async function delete_blob" in content

    def test_exa_mjs_has_exports(self):
        content = (CONNECTORS_DIR / "exa.mjs").read_text()
        assert "export async function search" in content
        assert "export async function get_contents" in content
        assert "export async function find_similar" in content

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_mjs_has_cli_dispatch(self, name):
        content = (CONNECTORS_DIR / f"{name}.mjs").read_text()
        assert "process.argv" in content, f"No CLI dispatch in {name}.mjs"

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_mjs_uses_abort_controller(self, name):
        content = (CONNECTORS_DIR / f"{name}.mjs").read_text()
        assert "AbortController" in content, f"No timeout/abort in {name}.mjs"


class TestSensitiveKeys:
    """New credential env vars must appear in routes._SENSITIVE_KEYS."""

    def setup_method(self):
        import sandcastle.api.routes as routes_mod
        self.sensitive = routes_mod._SENSITIVE_KEYS

    def test_langfuse_secret_key_is_sensitive(self):
        assert "tool_langfuse_secret_key" in self.sensitive

    def test_langfuse_public_key_is_sensitive(self):
        assert "tool_langfuse_public_key" in self.sensitive

    def test_qdrant_api_key_is_sensitive(self):
        assert "tool_qdrant_api_key" in self.sensitive

    def test_gcs_service_account_json_is_sensitive(self):
        assert "tool_gcs_service_account_json" in self.sensitive

    def test_azure_connection_string_is_sensitive(self):
        assert "tool_azure_storage_connection_string" in self.sensitive

    def test_azure_storage_key_is_sensitive(self):
        assert "tool_azure_storage_key" in self.sensitive

    def test_exa_api_key_is_sensitive(self):
        assert "tool_exa_api_key" in self.sensitive

    def test_sensitive_keys_is_frozenset(self):
        assert isinstance(self.sensitive, frozenset)


class TestRegistryConnectorFileField:
    """connector_file field must match actual filename on disk."""

    def setup_method(self):
        from sandcastle.engine.tools.registry import TOOL_REGISTRY
        self.registry = TOOL_REGISTRY

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_connector_file_matches_disk(self, name):
        tool = self.registry[name]
        expected_path = CONNECTORS_DIR / tool.connector_file
        assert expected_path.exists(), (
            f"connector_file '{tool.connector_file}' for '{name}' not found at {expected_path}"
        )


class TestKnownTools:
    """New connectors should appear in KNOWN_TOOLS."""

    def setup_method(self):
        from sandcastle.engine.tools.registry import KNOWN_TOOLS
        self.known = KNOWN_TOOLS

    @pytest.mark.parametrize("name", NEW_CONNECTORS)
    def test_in_known_tools(self, name):
        assert name in self.known, f"'{name}' not in KNOWN_TOOLS"


class TestValidCategories:
    """New categories (observability, storage) must appear in VALID_CATEGORIES."""

    def setup_method(self):
        from sandcastle.engine.tools.registry import VALID_CATEGORIES
        self.categories = VALID_CATEGORIES

    def test_observability_category_valid(self):
        assert "observability" in self.categories

    def test_storage_category_valid(self):
        assert "storage" in self.categories
