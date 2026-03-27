"""Tests for the Agent Marketplace MVP endpoints.

Covers:
- POST /hub/submit  - submit a workflow template
- GET  /hub/community - list community templates
- POST /hub/templates/{slug}/rate - rate a template
- POST /hub/templates/{slug}/download - track download
"""

from __future__ import annotations

import textwrap

from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


def _error_code(resp) -> str:
    """Extract the error code from a FastAPI HTTPException response."""
    return resp.json()["detail"]["error"]["code"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_YAML = textwrap.dedent("""\
    name: Test Marketplace Workflow
    description: A workflow for marketplace testing
    steps:
      - id: step1
        model: haiku
        prompt: "Summarize {{input}}"
      - id: step2
        model: sonnet
        tool: web_search
        depends_on: [step1]
        prompt: "Research {{input}}"
""")


def _submit(yaml_content: str = VALID_YAML, **kwargs) -> dict:
    """Helper: POST /hub/submit and return response JSON."""
    payload = {
        "yaml_content": yaml_content,
        "description": "A test workflow for unit testing",
        "category": "general_ai",
        "tags": ["test", "automation"],
        **kwargs,
    }
    return client.post("/api/hub/submit", json=payload)


# ---------------------------------------------------------------------------
# 1. Submit valid template -> pending status
# ---------------------------------------------------------------------------


class TestHubSubmit:
    """Tests for POST /hub/submit."""

    def test_submit_valid_template_returns_pending(self):
        resp = _submit()
        assert resp.status_code in (200, 201)
        data = resp.json()["data"]
        assert data["status"] == "pending"
        assert data["name"] == "Test Marketplace Workflow"
        assert data["step_count"] == 2
        assert data["author"] is not None
        assert "/" in data["slug"]

    def test_submit_extracts_models_used(self):
        resp = _submit()
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "haiku" in data["models_used"]
        assert "sonnet" in data["models_used"]

    def test_submit_extracts_tools_used(self):
        resp = _submit()
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "web_search" in data["tools_used"]

    def test_submit_stores_tags_and_category(self):
        resp = _submit(tags=["marketing", "crm"], category="marketing")
        assert resp.status_code in (200, 201)
        data = resp.json()["data"]
        assert data["category"] == "marketing"
        assert "marketing" in data["tags"]
        assert "crm" in data["tags"]

    def test_submit_returns_slug_with_author_prefix(self):
        resp = _submit()
        assert resp.status_code in (200, 201)
        slug = resp.json()["data"]["slug"]
        # Slug must be "author/name" format
        assert "/" in slug
        parts = slug.split("/")
        assert len(parts) == 2
        assert parts[0]
        assert parts[1]

    def test_submit_returns_id_and_created_at(self):
        resp = _submit()
        assert resp.status_code in (200, 201)
        data = resp.json()["data"]
        assert data["id"]
        assert data["created_at"]

    def test_submit_initial_downloads_is_zero(self):
        resp = _submit()
        assert resp.status_code in (200, 201)
        assert resp.json()["data"]["downloads"] == 0

    def test_submit_initial_rating_is_null(self):
        resp = _submit()
        assert resp.status_code in (200, 201)
        assert resp.json()["data"]["rating"] is None
        assert resp.json()["data"]["rating_count"] == 0


# ---------------------------------------------------------------------------
# 2. Submit invalid YAML -> 400
# ---------------------------------------------------------------------------


class TestHubSubmitInvalidYaml:
    """Verify bad YAML is rejected with 400."""

    def test_submit_invalid_yaml_returns_400(self):
        resp = _submit(yaml_content="this: is: not: valid: yaml: !!!")
        assert resp.status_code == 400
        assert _error_code(resp) == "INVALID_YAML"

    def test_submit_non_mapping_yaml_returns_400(self):
        resp = _submit(yaml_content="- item1\n- item2\n")
        assert resp.status_code == 400
        assert _error_code(resp) == "INVALID_YAML"

    def test_submit_empty_yaml_returns_400(self):
        resp = client.post(
            "/api/hub/submit",
            json={"yaml_content": "", "description": "desc", "category": "general_ai", "tags": []},
        )
        assert resp.status_code == 400

    def test_submit_invalid_json_returns_400(self):
        resp = client.post("/api/hub/submit", content="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_submit_missing_description_returns_400(self):
        resp = client.post(
            "/api/hub/submit",
            json={"yaml_content": VALID_YAML, "category": "general_ai"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 3. List community templates with filters
# ---------------------------------------------------------------------------


class TestHubCommunity:
    """Tests for GET /hub/community."""

    def _ensure_approved_submission(self) -> str:
        """Submit a template and manually mark it approved. Returns slug."""
        import asyncio
        from sqlalchemy import select
        from sandcastle.models.db import HubSubmission, async_session

        resp = _submit()
        assert resp.status_code in (200, 201)
        slug = resp.json()["data"]["slug"]

        # Approve it directly in DB
        async def _approve():
            async with async_session() as session:
                result = await session.execute(
                    select(HubSubmission).where(HubSubmission.slug == slug)
                )
                sub = result.scalar_one()
                sub.status = "approved"
                await session.commit()

        asyncio.run(_approve())
        return slug

    def test_list_defaults_to_approved(self):
        slug = self._ensure_approved_submission()
        resp = client.get("/api/hub/community")
        assert resp.status_code in (200, 201)
        data = resp.json()["data"]
        slugs = [item["slug"] for item in data]
        assert slug in slugs

    def test_list_pending_excludes_approved(self):
        self._ensure_approved_submission()
        resp = client.get("/api/hub/community?status=pending")
        assert resp.status_code in (200, 201)
        # All returned items should be pending
        for item in resp.json()["data"]:
            assert item["status"] == "pending"

    def test_list_returns_pagination_meta(self):
        resp = client.get("/api/hub/community")
        assert resp.status_code in (200, 201)
        meta = resp.json().get("meta")
        assert meta is not None
        assert "total" in meta
        assert "limit" in meta
        assert "offset" in meta

    def test_list_category_filter(self):
        # Submit a marketing template and approve it
        import asyncio
        from sqlalchemy import select
        from sandcastle.models.db import HubSubmission, async_session

        resp = _submit(category="marketing")
        slug = resp.json()["data"]["slug"]

        async def _approve():
            async with async_session() as session:
                result = await session.execute(
                    select(HubSubmission).where(HubSubmission.slug == slug)
                )
                sub = result.scalar_one()
                sub.status = "approved"
                sub.category = "marketing"
                await session.commit()

        asyncio.run(_approve())

        resp = client.get("/api/hub/community?category=marketing")
        assert resp.status_code in (200, 201)
        for item in resp.json()["data"]:
            assert item["category"] == "marketing"

    def test_list_limit_and_offset(self):
        resp = client.get("/api/hub/community?limit=1&offset=0")
        assert resp.status_code in (200, 201)
        data = resp.json()["data"]
        assert len(data) <= 1

    def test_list_invalid_status_returns_400(self):
        resp = client.get("/api/hub/community?status=invalid_status")
        assert resp.status_code == 400
        assert _error_code(resp) == "INVALID_STATUS"

    def test_list_limit_capped_at_100(self):
        resp = client.get("/api/hub/community?limit=200")
        # FastAPI Query validator should reject limit > 100
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. Rate template (1-5)
# ---------------------------------------------------------------------------


class TestRateTemplate:
    """Tests for POST /hub/templates/{slug}/rate."""

    def _create_and_get_slug(self) -> str:
        resp = _submit()
        assert resp.status_code in (200, 201)
        return resp.json()["data"]["slug"]

    def test_rate_template_1_to_5(self):
        slug = self._create_and_get_slug()
        for star in [1, 2, 3, 4, 5]:
            resp = client.post(f"/api/hub/templates/{slug}/rate", json={"rating": star})
            assert resp.status_code in (200, 201), f"Failed for rating={star}"
            data = resp.json()["data"]
            assert data["slug"] == slug
            assert data["rating"] is not None
            assert 1.0 <= data["rating"] <= 5.0

    def test_rate_template_running_average(self):
        slug = self._create_and_get_slug()
        # First rating: 4
        resp = client.post(f"/api/hub/templates/{slug}/rate", json={"rating": 4})
        assert resp.status_code in (200, 201)
        assert resp.json()["data"]["rating"] == 4.0
        assert resp.json()["data"]["rating_count"] == 1

        # Second rating: 2 -> average should be 3.0
        resp = client.post(f"/api/hub/templates/{slug}/rate", json={"rating": 2})
        assert resp.status_code in (200, 201)
        assert resp.json()["data"]["rating"] == 3.0
        assert resp.json()["data"]["rating_count"] == 2

    def test_rate_template_nonexistent_returns_404(self):
        resp = client.post("/api/hub/templates/nobody/no-such-workflow/rate", json={"rating": 3})
        assert resp.status_code == 404
        assert _error_code(resp) == "NOT_FOUND"

    def test_rate_template_rating_0_returns_400(self):
        slug = self._create_and_get_slug()
        resp = client.post(f"/api/hub/templates/{slug}/rate", json={"rating": 0})
        assert resp.status_code == 400

    def test_rate_template_rating_6_returns_400(self):
        slug = self._create_and_get_slug()
        resp = client.post(f"/api/hub/templates/{slug}/rate", json={"rating": 6})
        assert resp.status_code == 400

    def test_rate_template_missing_rating_field_returns_400(self):
        slug = self._create_and_get_slug()
        resp = client.post(f"/api/hub/templates/{slug}/rate", json={})
        assert resp.status_code == 400

    def test_rate_template_invalid_json_returns_400(self):
        slug = self._create_and_get_slug()
        resp = client.post(
            f"/api/hub/templates/{slug}/rate",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. Download tracking increments count
# ---------------------------------------------------------------------------


class TestDownloadTracking:
    """Tests for POST /hub/templates/{slug}/download."""

    def _create_and_get_slug(self) -> str:
        resp = _submit()
        assert resp.status_code in (200, 201)
        return resp.json()["data"]["slug"]

    def test_download_tracking_increments_count(self):
        slug = self._create_and_get_slug()

        resp1 = client.post(f"/api/hub/templates/{slug}/download")
        assert resp1.status_code in (200, 201)
        assert resp1.json()["data"]["downloads"] == 1

        resp2 = client.post(f"/api/hub/templates/{slug}/download")
        assert resp2.status_code in (200, 201)
        assert resp2.json()["data"]["downloads"] == 2

        resp3 = client.post(f"/api/hub/templates/{slug}/download")
        assert resp3.status_code == 200
        assert resp3.json()["data"]["downloads"] == 3

    def test_download_tracking_returns_slug(self):
        slug = self._create_and_get_slug()
        resp = client.post(f"/api/hub/templates/{slug}/download")
        assert resp.status_code in (200, 201)
        assert resp.json()["data"]["slug"] == slug

    def test_download_tracking_nonexistent_returns_404(self):
        resp = client.post("/api/hub/templates/nobody/ghost-workflow/download")
        assert resp.status_code == 404
        assert _error_code(resp) == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 6. Duplicate slug prevention
# ---------------------------------------------------------------------------


class TestDuplicateSlugPrevention:
    """Verify that submitting the same workflow name twice produces unique slugs."""

    def test_duplicate_name_gets_unique_slug(self):
        yaml_content = textwrap.dedent("""\
            name: Duplicate Test Workflow
            steps:
              - id: step1
                model: haiku
                prompt: "test"
        """)

        resp1 = _submit(yaml_content=yaml_content)
        resp2 = _submit(yaml_content=yaml_content)

        assert resp1.status_code == 201
        assert resp2.status_code == 201

        slug1 = resp1.json()["data"]["slug"]
        slug2 = resp2.json()["data"]["slug"]
        assert slug1 != slug2, "Duplicate slugs were generated"
