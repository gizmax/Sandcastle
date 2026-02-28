"""Tests for the Community Hub registry, API endpoints, and CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)

HUB_DIR = Path(__file__).resolve().parent.parent / "hub"
REGISTRY_PATH = HUB_DIR / "registry.json"
SCHEMA_PATH = HUB_DIR / "schema" / "template.schema.json"


# ---------------------------------------------------------------------------
# Registry data model tests
# ---------------------------------------------------------------------------


class TestRegistryStructure:
    """Validate the hub registry JSON structure."""

    @pytest.fixture(autouse=True)
    def load_registry(self):
        with open(REGISTRY_PATH) as f:
            self.registry = json.load(f)

    def test_registry_has_required_top_level_keys(self):
        assert "version" in self.registry
        assert "templates" in self.registry
        assert "categories" in self.registry
        assert "stats" in self.registry
        assert "collections" in self.registry

    def test_registry_version_is_2(self):
        assert self.registry["version"] == 2

    def test_templates_count(self):
        templates = self.registry["templates"]
        assert len(templates) >= 35  # at least built-in templates
        assert len(templates) == self.registry["stats"]["total_templates"]

    def test_each_template_has_required_fields(self):
        required = {"slug", "name", "description", "author", "version", "category"}
        for t in self.registry["templates"]:
            missing = required - set(t.keys())
            assert not missing, f"Template {t.get('slug', '?')} missing: {missing}"

    def test_each_template_has_new_fields(self):
        new_fields = {"estimated_cost_per_run", "avg_execution_time", "downloads", "remix_count"}
        for t in self.registry["templates"]:
            missing = new_fields - set(t.keys())
            assert not missing, f"Template {t.get('slug', '?')} missing new fields: {missing}"

    def test_cost_is_positive(self):
        for t in self.registry["templates"]:
            cost = t.get("estimated_cost_per_run", 0)
            assert cost > 0, f"{t['slug']} has zero cost"

    def test_downloads_non_negative(self):
        for t in self.registry["templates"]:
            assert t.get("downloads", 0) >= 0

    def test_remix_count_non_negative(self):
        for t in self.registry["templates"]:
            assert t.get("remix_count", 0) >= 0

    def test_forked_from_is_valid_slug_or_null(self):
        slugs = {t["slug"] for t in self.registry["templates"]}
        for t in self.registry["templates"]:
            forked = t.get("forked_from")
            if forked is not None:
                assert forked in slugs, f"{t['slug']} forked_from '{forked}' not in registry"

    def test_categories_match_templates(self):
        cat_counts = {}
        for t in self.registry["templates"]:
            cat = t["category"]
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        for cat_entry in self.registry["categories"]:
            cid = cat_entry["id"]
            assert cat_entry["count"] == cat_counts.get(cid, 0), (
                f"Category {cid}: expected {cat_counts.get(cid, 0)}, got {cat_entry['count']}"
            )

    def test_community_templates_exist(self):
        community = [t for t in self.registry["templates"] if t.get("source") == "community"]
        assert len(community) >= 1, "No community templates in registry"

    def test_multiple_authors(self):
        authors = {t["author"] for t in self.registry["templates"]}
        assert len(authors) > 1, "Only one author in registry"
        assert self.registry["stats"]["total_authors"] == len(authors)


class TestCollections:
    """Validate curated collections in registry."""

    @pytest.fixture(autouse=True)
    def load_registry(self):
        with open(REGISTRY_PATH) as f:
            self.registry = json.load(f)

    def test_collections_exist(self):
        assert len(self.registry["collections"]) >= 3

    def test_collection_has_required_fields(self):
        required = {"id", "name", "description", "template_slugs"}
        for col in self.registry["collections"]:
            missing = required - set(col.keys())
            assert not missing, f"Collection {col.get('id', '?')} missing: {missing}"

    def test_collection_slugs_reference_valid_templates(self):
        slugs = {t["slug"] for t in self.registry["templates"]}
        for col in self.registry["collections"]:
            for slug in col["template_slugs"]:
                assert slug in slugs, (
                    f"Collection {col['id']} references unknown slug: {slug}"
                )

    def test_collection_has_at_least_3_templates(self):
        for col in self.registry["collections"]:
            assert len(col["template_slugs"]) >= 3, (
                f"Collection {col['id']} has only {len(col['template_slugs'])} templates"
            )

    def test_collection_ids_unique(self):
        ids = [col["id"] for col in self.registry["collections"]]
        assert len(ids) == len(set(ids))


class TestSchema:
    """Validate the template JSON schema."""

    @pytest.fixture(autouse=True)
    def load_schema(self):
        with open(SCHEMA_PATH) as f:
            self.schema = json.load(f)

    def test_schema_has_new_fields(self):
        props = self.schema["properties"]
        assert "estimated_cost_per_run" in props
        assert "avg_execution_time" in props
        assert "downloads" in props
        assert "remix_count" in props
        assert "forked_from" in props
        assert "source" in props

    def test_cost_field_is_number(self):
        assert self.schema["properties"]["estimated_cost_per_run"]["type"] == "number"

    def test_forked_from_allows_null(self):
        ff = self.schema["properties"]["forked_from"]
        assert "null" in ff["type"] or ff["type"] == ["string", "null"]


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestHubRegistryEndpoint:
    """Test GET /api/hub/registry."""

    def setup_method(self):
        """Clear hub cache before each test."""
        from sandcastle.api import routes
        routes._hub_cache.clear()

    def test_hub_registry_returns_data(self):
        # The endpoint proxies GitHub - mock the HTTP call
        mock_registry = {
            "version": 2,
            "templates": [{"slug": "test/test", "name": "Test"}],
            "categories": [],
            "stats": {"total_templates": 1},
            "collections": [],
        }

        with patch("sandcastle.api.routes.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = mock_registry
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            response = client.get("/api/hub/registry")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["version"] == 2
        assert len(data["templates"]) == 1

    def test_hub_registry_fallback_on_error(self):
        with patch("sandcastle.api.routes.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("Network error"))
            mock_httpx.AsyncClient.return_value = mock_client

            response = client.get("/api/hub/registry")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["templates"] == []


class TestHubCollectionsEndpoint:
    """Test GET /api/hub/collections."""

    def setup_method(self):
        """Clear hub cache before each test."""
        from sandcastle.api import routes
        routes._hub_cache.clear()

    def test_hub_collections_returns_enriched_data(self):
        mock_registry = {
            "templates": [
                {"slug": "gizmax/lead-scoring", "name": "Lead Scoring", "step_count": 4},
            ],
            "collections": [
                {
                    "id": "sales-stack",
                    "name": "Sales Stack",
                    "description": "Sales workflows",
                    "template_slugs": ["gizmax/lead-scoring"],
                    "downloads": 100,
                },
            ],
        }

        with patch("sandcastle.api.routes.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = mock_registry
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value = mock_client

            response = client.get("/api/hub/collections")

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["template_count"] == 1
        assert data[0]["templates"][0]["slug"] == "gizmax/lead-scoring"


class TestHubPlaygroundEndpoint:
    """Test POST /api/hub/playground."""

    def test_playground_returns_simulated_result(self):
        response = client.post(
            "/api/hub/playground",
            json={
                "slug": "gizmax/lead-scoring",
                "inputs": {"company": "Acme"},
                "step_count": 4,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "completed"
        assert data["slug"] == "gizmax/lead-scoring"
        assert data["steps_completed"] == 4
        assert "output" in data


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestHubCLI:
    """Test hub CLI command functions."""

    def test_hub_search_function_exists(self):
        from sandcastle.__main__ import _cmd_hub_search
        assert callable(_cmd_hub_search)

    def test_hub_install_function_exists(self):
        from sandcastle.__main__ import _cmd_hub_install
        assert callable(_cmd_hub_install)

    def test_hub_list_function_exists(self):
        from sandcastle.__main__ import _cmd_hub_list
        assert callable(_cmd_hub_list)

    def test_hub_publish_function_exists(self):
        from sandcastle.__main__ import _cmd_hub_publish
        assert callable(_cmd_hub_publish)

    def test_hub_collections_function_exists(self):
        from sandcastle.__main__ import _cmd_hub_collections
        assert callable(_cmd_hub_collections)

    def test_hub_install_collection_function_exists(self):
        from sandcastle.__main__ import _cmd_hub_install_collection
        assert callable(_cmd_hub_install_collection)


# ---------------------------------------------------------------------------
# Sanitization tests
# ---------------------------------------------------------------------------


class TestWorkflowSanitization:
    """Test YAML sanitization for safe sharing."""

    def test_env_vars_redacted(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml = """
steps:
  - id: call-api
    prompt: "Call with key ${API_KEY} and secret $SECRET_TOKEN"
"""
        result = _sanitize_workflow_yaml(yaml)
        assert "${API_KEY}" not in result
        assert "$SECRET_TOKEN" not in result
        assert "<REDACTED>" in result

    def test_password_lines_redacted(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml = """
config:
  password: my-super-secret
  api_key: sk-12345
  token: tok-abc
"""
        result = _sanitize_workflow_yaml(yaml)
        assert "my-super-secret" not in result
        assert "sk-12345" not in result
        assert "tok-abc" not in result

    def test_variable_references_preserved(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml = """
steps:
  - id: step1
    prompt: "Process {input.data}"
    api_key: <REDACTED>
"""
        result = _sanitize_workflow_yaml(yaml)
        assert "{input.data}" in result
        assert "<REDACTED>" in result
