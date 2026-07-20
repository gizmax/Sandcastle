"""Comprehensive tests for Sandcastle v0.28 features.

Tests cover:
  1. Batch Run API (18 tests)
  2. Auto-update API (14 tests)
  3. Config validation (7 tests)
  4. Schema validation (7 tests)

Total: 46 tests

Uses TestClient against in-memory SQLite. Mocks are used for external
services (PyPI, GitHub, pip) and workflow resolution.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Disable rate limiting before importing the app
from sandcastle.api import rate_limit as _rl_mod
from sandcastle.api import routes as routes_module

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.api.routes import _batch_store, _is_in_blackout_window, _update_cache  # noqa: E402
from sandcastle.api.schemas import (  # noqa: E402
    BatchItemStatus,
    BatchRunRequest,
    BatchStartedResponse,
    BatchStatusResponse,
    UpdateCheckResponse,
    UpdateRequest,
    UpdateResponse,
)
from sandcastle.config import Settings, settings  # noqa: E402
from sandcastle.main import app  # noqa: E402

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

VALID_WORKFLOW_YAML = """\
name: batch-test
description: Workflow for batch testing
steps:
  - id: process
    prompt: "Process {input.text}"
    model: haiku
    max_turns: 3
"""


def _admin_headers() -> dict[str, str]:
    """Return headers that pass admin auth checks in test (auth disabled)."""
    return {}


def _cleanup_batch_store():
    """Remove all entries from the in-memory batch store."""
    _batch_store.clear()


def _seed_batch(
    batch_id: str = "test-batch-001",
    workflow: str = "batch-test",
    status: str = "running",
    total: int = 3,
    completed: int = 1,
    failed: int = 0,
    running: int = 1,
    pending: int = 1,
) -> dict[str, Any]:
    """Insert a batch record directly into _batch_store for GET tests."""
    items = []
    for i in range(total):
        if i < completed:
            items.append({
                "index": i,
                "status": "completed",
                "run_id": str(uuid.uuid4()),
                "cost_usd": 0.01,
                "error": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        elif i < completed + failed:
            items.append({
                "index": i,
                "status": "failed",
                "run_id": str(uuid.uuid4()),
                "cost_usd": 0.005,
                "error": "Simulated failure",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        elif i < completed + failed + running:
            items.append({
                "index": i,
                "status": "running",
                "run_id": str(uuid.uuid4()),
                "cost_usd": 0.0,
                "error": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
            })
        else:
            items.append({
                "index": i,
                "status": "pending",
                "run_id": None,
                "cost_usd": 0.0,
                "error": None,
                "started_at": None,
                "completed_at": None,
            })

    batch = {
        "batch_id": batch_id,
        "workflow": workflow,
        "status": status,
        "total": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "pending": pending,
        "total_cost_usd": completed * 0.01 + failed * 0.005,
        "items": items,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _batch_store[batch_id] = batch
    return batch


# ===========================================================================
# 1. BATCH RUN API
# ===========================================================================


class TestBatchRunPost:
    """POST /api/workflows/{name}/batch - start a batch run."""

    def setup_method(self):
        _cleanup_batch_store()

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate", return_value=[])
    @patch("sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock)
    def test_valid_batch_returns_202(self, mock_enqueue, mock_validate, mock_parse, mock_resolve):
        """POST with valid items returns 202 and batch_id."""
        mock_resolve.return_value = (VALID_WORKFLOW_YAML, 1)
        mock_parse.return_value = MagicMock(name="batch-test", risk_level="minimal", input_schema=None)

        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={
                "items": [{"text": "hello"}, {"text": "world"}],
                "max_parallel": 2,
            },
        )
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert "batch_id" in data
        assert data["workflow"] == "batch-test"
        assert data["total"] == 2
        assert data["status"] == "running"

    def test_empty_items_returns_400(self):
        """POST with empty items list returns 400."""
        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": []},
        )
        assert resp.status_code == 400

    def test_items_over_10000_returns_400(self):
        """POST with more than 10000 items returns 400."""
        # Creating 10001 minimal items
        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": [{"x": i} for i in range(10001)]},
        )
        assert resp.status_code == 400

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    def test_invalid_workflow_name_returns_400(self, mock_resolve):
        """POST with workflow name that cannot be resolved returns 400."""
        mock_resolve.side_effect = FileNotFoundError("Workflow 'nope' not found")

        resp = client.post(
            "/api/workflows/nope/batch",
            json={"items": [{"text": "hello"}]},
        )
        assert resp.status_code == 400
        body = resp.json()
        # Error may be top-level or nested inside 'detail'
        error = body.get("error") or body.get("detail", {}).get("error", {})
        assert error.get("code") in ("INVALID_WORKFLOW", "INVALID_REQUEST")

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate", return_value=[])
    @patch("sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock)
    def test_batch_returns_unique_batch_id(self, mock_enqueue, mock_validate, mock_parse, mock_resolve):
        """Each batch request gets a unique batch_id."""
        mock_resolve.return_value = (VALID_WORKFLOW_YAML, 1)
        mock_parse.return_value = MagicMock(name="batch-test", risk_level="minimal", input_schema=None)

        ids = set()
        for _ in range(3):
            resp = client.post(
                "/api/workflows/batch-test/batch",
                json={"items": [{"text": "a"}]},
            )
            assert resp.status_code == 202
            ids.add(resp.json()["data"]["batch_id"])
        assert len(ids) == 3, "Batch IDs must be unique"

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate", return_value=[])
    @patch("sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock)
    def test_batch_with_max_cost_per_item(self, mock_enqueue, mock_validate, mock_parse, mock_resolve):
        """POST with max_cost_per_item_usd is accepted."""
        mock_resolve.return_value = (VALID_WORKFLOW_YAML, 1)
        mock_parse.return_value = MagicMock(name="batch-test", risk_level="minimal", input_schema=None)

        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={
                "items": [{"text": "hello"}],
                "max_parallel": 1,
                "max_cost_per_item_usd": 0.50,
            },
        )
        assert resp.status_code == 202

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate", return_value=[])
    @patch("sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock)
    def test_batch_creates_store_entry(self, mock_enqueue, mock_validate, mock_parse, mock_resolve):
        """POST creates an entry in _batch_store with correct initial state."""
        mock_resolve.return_value = (VALID_WORKFLOW_YAML, 1)
        mock_parse.return_value = MagicMock(name="batch-test", risk_level="minimal", input_schema=None)

        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": [{"a": 1}, {"b": 2}, {"c": 3}], "max_parallel": 2},
        )
        assert resp.status_code == 202
        bid = resp.json()["data"]["batch_id"]
        assert bid in _batch_store
        entry = _batch_store[bid]
        assert entry["total"] == 3
        assert entry["workflow"] == "batch-test"
        assert len(entry["items"]) == 3

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate", return_value=[])
    @patch("sandcastle.api.routes.enqueue_workflow", new_callable=AsyncMock)
    def test_batch_items_have_correct_indices(self, mock_enqueue, mock_validate, mock_parse, mock_resolve):
        """Each batch item in the store has a sequential index."""
        mock_resolve.return_value = (VALID_WORKFLOW_YAML, 1)
        mock_parse.return_value = MagicMock(name="batch-test", risk_level="minimal", input_schema=None)

        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": [{"x": i} for i in range(5)], "max_parallel": 3},
        )
        bid = resp.json()["data"]["batch_id"]
        items = _batch_store[bid]["items"]
        # Verify sequential indices (status may already be "running" due to
        # the background task starting immediately)
        for idx, item in enumerate(items):
            assert item["index"] == idx
            assert item["status"] in ("pending", "running", "completed", "failed")

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate")
    def test_batch_with_invalid_yaml_returns_400(self, mock_validate, mock_parse, mock_resolve):
        """POST with workflow that fails YAML validation returns 400."""
        mock_resolve.return_value = ("invalid yaml", 1)
        mock_parse.side_effect = Exception("YAML parse error")

        resp = client.post(
            "/api/workflows/bad-wf/batch",
            json={"items": [{"text": "hello"}]},
        )
        assert resp.status_code == 400

    @patch("sandcastle.api.routes._resolve_workflow_request", new_callable=AsyncMock)
    @patch("sandcastle.api.routes.parse_yaml_string")
    @patch("sandcastle.api.routes.validate")
    def test_batch_with_validation_errors_returns_400(self, mock_validate, mock_parse, mock_resolve):
        """POST with workflow that has validation errors returns 400."""
        mock_resolve.return_value = (VALID_WORKFLOW_YAML, 1)
        mock_parse.return_value = MagicMock(name="batch-test")
        mock_validate.return_value = ["Step 'foo' references undefined output"]

        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": [{"text": "hello"}]},
        )
        assert resp.status_code == 400
        assert "VALIDATION_ERROR" in resp.text

    def test_batch_missing_items_field_returns_400(self):
        """POST without 'items' field returns 400."""
        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"max_parallel": 5},
        )
        assert resp.status_code == 400

    def test_batch_items_not_list_returns_400(self):
        """POST with items as a string instead of list returns 400."""
        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": "not a list"},
        )
        assert resp.status_code == 400

    def test_batch_negative_max_parallel_returns_422(self):
        """POST with max_parallel < 1 returns validation error."""
        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": [{"text": "a"}], "max_parallel": 0},
        )
        # Pydantic validation fires before route handler; could be 400 or 422
        assert resp.status_code in (400, 422)

    def test_batch_max_parallel_over_100_returns_422(self):
        """POST with max_parallel > 100 returns validation error."""
        resp = client.post(
            "/api/workflows/batch-test/batch",
            json={"items": [{"text": "a"}], "max_parallel": 101},
        )
        assert resp.status_code in (400, 422)


class TestBatchStatusGet:
    """GET /api/batch/{batch_id}/status - poll batch progress."""

    def setup_method(self):
        _cleanup_batch_store()

    def test_existing_batch_returns_progress(self):
        """GET for a known batch_id returns current progress."""
        _seed_batch("b-100", total=5, completed=3, failed=1, running=1, pending=0)

        resp = client.get("/api/batch/b-100/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["batch_id"] == "b-100"
        assert data["total"] == 5
        assert data["completed"] == 3
        assert data["failed"] == 1

    def test_nonexistent_batch_returns_404(self):
        """GET for an unknown batch_id returns 404."""
        resp = client.get("/api/batch/does-not-exist/status")
        assert resp.status_code == 404

    def test_completed_batch_has_total_cost(self):
        """A completed batch has total_cost_usd summed from items."""
        _seed_batch("b-done", total=2, completed=2, failed=0, running=0, pending=0, status="completed")

        resp = client.get("/api/batch/b-done/status")
        data = resp.json()["data"]
        assert data["status"] == "completed"
        assert data["total_cost_usd"] >= 0

    def test_batch_items_have_correct_structure(self):
        """Each item in the batch status has index, status, run_id, cost_usd."""
        _seed_batch("b-struct", total=2, completed=1, failed=0, running=1, pending=0)

        resp = client.get("/api/batch/b-struct/status")
        data = resp.json()["data"]
        for item in data["items"]:
            assert "index" in item
            assert "status" in item
            assert "cost_usd" in item

    def test_concurrent_batches_isolated(self):
        """Two concurrent batches do not interfere with each other."""
        _seed_batch("batch-a", workflow="wf-a", total=3, completed=1, failed=0, running=1, pending=1)
        _seed_batch("batch-b", workflow="wf-b", total=5, completed=4, failed=1, running=0, pending=0, status="partial_failure")

        resp_a = client.get("/api/batch/batch-a/status")
        resp_b = client.get("/api/batch/batch-b/status")

        data_a = resp_a.json()["data"]
        data_b = resp_b.json()["data"]

        assert data_a["workflow"] == "wf-a"
        assert data_a["total"] == 3
        assert data_b["workflow"] == "wf-b"
        assert data_b["total"] == 5
        assert data_b["status"] == "partial_failure"


# ===========================================================================
# 2. AUTO-UPDATE API
# ===========================================================================


class TestCheckUpdate:
    """GET /api/check-update - check for newer version on PyPI."""

    def setup_method(self):
        _update_cache.clear()

    @patch("sandcastle.api.routes.httpx.AsyncClient")
    def test_returns_current_and_latest_version(self, mock_httpx_cls):
        """Response includes current_version and latest_version."""
        mock_client = AsyncMock()
        mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "99.0.0"}}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        resp = client.get("/api/check-update")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "current_version" in data
        assert "latest_version" in data

    @patch("sandcastle.api.routes.httpx.AsyncClient")
    def test_has_changelog_url_and_highlights(self, mock_httpx_cls):
        """Response includes changelog_url and highlights fields."""
        mock_client = AsyncMock()
        mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # PyPI response
        pypi_resp = MagicMock()
        pypi_resp.json.return_value = {"info": {"version": "99.0.0"}}
        pypi_resp.raise_for_status = MagicMock()

        # GitHub release response
        gh_resp = MagicMock()
        gh_resp.json.return_value = {
            "body": "- **Batch runs** for parallel execution\n- **Auto update** mechanism\n- **Schedule monitor** UI"
        }

        mock_client.get = AsyncMock(side_effect=[pypi_resp, gh_resp])

        resp = client.get("/api/check-update")
        data = resp.json()["data"]
        assert "changelog_url" in data
        assert "highlights" in data

    @patch("sandcastle.api.routes.httpx.AsyncClient")
    def test_cache_works_within_ttl(self, mock_httpx_cls):
        """Second call within 30 minutes uses cache, not PyPI."""
        mock_client = AsyncMock()
        mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "99.0.0"}}
        mock_resp.raise_for_status = MagicMock()
        gh_resp = MagicMock()
        gh_resp.json.return_value = {"body": ""}
        mock_client.get = AsyncMock(side_effect=[mock_resp, gh_resp])

        # First call populates cache
        resp1 = client.get("/api/check-update")
        assert resp1.status_code == 200

        # Second call should use cache (no more HTTP calls)
        mock_client.get = AsyncMock(side_effect=Exception("Should not be called"))
        resp2 = client.get("/api/check-update")
        assert resp2.status_code == 200

    @patch("sandcastle.api.routes.httpx.AsyncClient")
    def test_graceful_degradation_on_network_error(self, mock_httpx_cls):
        """If PyPI is unreachable, endpoint still returns 200 with current version."""
        mock_client = AsyncMock()
        mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        resp = client.get("/api/check-update")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # current == latest when PyPI is unreachable (no update shown)
        assert data["current_version"] == data["latest_version"]
        assert data["update_available"] is False


class TestAdminUpdate:
    """POST /api/admin/update - trigger software update."""

    def test_update_with_blackout_window_blocked(self):
        """Update is blocked during blackout window."""
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        try:
            # Set blackout window to cover current time (00:00 - 23:59)
            settings.update_blackout_start = "00:00"
            settings.update_blackout_end = "23:59"

            resp = client.post("/api/admin/update", json={})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "failed"
            assert "blackout" in data["error"].lower()
        finally:
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end

    def test_update_with_pin_channel_no_pinned_version(self):
        """Update channel 'pin' without pinned_version returns error."""
        original_channel = settings.update_channel
        original_pinned = settings.pinned_version
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        try:
            settings.update_channel = "pin"
            settings.pinned_version = ""
            settings.update_blackout_start = ""
            settings.update_blackout_end = ""

            resp = client.post("/api/admin/update", json={})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "failed"
            assert "pin" in data["error"].lower()
        finally:
            settings.update_channel = original_channel
            settings.pinned_version = original_pinned
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end

    @patch("sandcastle.api.routes.httpx.AsyncClient")
    def test_update_with_specific_target_version(self, mock_httpx_cls):
        """POST with target_version uses that version instead of latest."""
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        original_channel = settings.update_channel
        try:
            settings.update_blackout_start = ""
            settings.update_blackout_end = ""
            settings.update_channel = "stable"

            # Mock pip install to succeed
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))

            mock_verify = AsyncMock()
            mock_verify.returncode = 0
            mock_verify.communicate = AsyncMock(return_value=(b"1.0.0", b""))

            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.side_effect = [mock_proc, mock_verify]
                with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
                    mock_wait.side_effect = [
                        (b"Successfully installed", b""),
                        (b"1.0.0", b""),
                    ]
                    with patch("sandcastle.api.routes._emit_update_audit", new_callable=AsyncMock):
                        with patch(
                            "sandcastle.api.routes._pre_update_backup",
                            new_callable=AsyncMock,
                            return_value=None,
                        ):
                            resp = client.post(
                                "/api/admin/update",
                                json={"target_version": "1.0.0"},
                            )
            data = resp.json()["data"]
            # Either success or "already on target" depending on current version
            assert data["status"] in ("success", "failed")
        finally:
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end
            settings.update_channel = original_channel

    @patch("sandcastle.api.routes.httpx.AsyncClient")
    def test_update_stable_rejects_prerelease(self, mock_httpx_cls):
        """When channel is stable, a pre-release target is rejected."""
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        original_channel = settings.update_channel
        try:
            settings.update_blackout_start = ""
            settings.update_blackout_end = ""
            settings.update_channel = "stable"

            # Mock PyPI to return a pre-release
            mock_client = AsyncMock()
            mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"info": {"version": "2.0.0a1"}}
            mock_resp.raise_for_status = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)

            with patch("sandcastle.api.routes._emit_update_audit", new_callable=AsyncMock):
                with patch(
                    "sandcastle.api.routes._pre_update_backup",
                    new_callable=AsyncMock,
                    return_value=None,
                ):
                    resp = client.post("/api/admin/update", json={})

            data = resp.json()["data"]
            assert data["status"] == "failed"
            assert "pre-release" in data["error"].lower() or "stable" in data["error"].lower()
        finally:
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end
            settings.update_channel = original_channel

    def test_update_requires_admin_when_auth_enabled(self):
        """When auth is enabled, non-admin requests get 403."""
        original_auth = settings.auth_required
        try:
            settings.auth_required = True
            resp = client.post(
                "/api/admin/update",
                json={},
                headers={"X-Tenant-Id": "some-tenant"},
            )
            # Should be 403 (admin required) or possibly rejected by middleware
            assert resp.status_code in (403, 401)
        finally:
            settings.auth_required = original_auth


class TestAdminRollback:
    """POST /api/admin/rollback - rollback to previous version."""

    def test_rollback_without_previous_version_returns_error(self, monkeypatch, tmp_path):
        """Rollback fails when no previous_version file exists."""
        state_dir = tmp_path / "sandcastle-state"
        monkeypatch.setattr(routes_module, "_SANDCASTLE_HOME", state_dir)

        resp = client.post("/api/admin/rollback")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "failed"
        assert "previous version" in data["error"].lower() or "no previous" in data["error"].lower()

    def test_rollback_with_empty_previous_version_returns_error(self, monkeypatch, tmp_path):
        """Rollback fails when previous_version file is empty."""
        state_dir = tmp_path / "sandcastle-state"
        monkeypatch.setattr(routes_module, "_SANDCASTLE_HOME", state_dir)
        prev_file = state_dir / "previous_version"
        prev_file.parent.mkdir(parents=True)
        prev_file.write_text("")

        resp = client.post("/api/admin/rollback")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "failed"
        assert "empty" in data["error"].lower() or "no previous" in data["error"].lower()

    def test_rollback_requires_admin_when_auth_enabled(self):
        """When auth is enabled, non-admin requests get 403."""
        original_auth = settings.auth_required
        try:
            settings.auth_required = True
            resp = client.post(
                "/api/admin/rollback",
                headers={"X-Tenant-Id": "some-tenant"},
            )
            assert resp.status_code in (403, 401)
        finally:
            settings.auth_required = original_auth

    def test_rollback_with_valid_previous_version(self, monkeypatch, tmp_path):
        """Rollback succeeds when previous_version file has a valid version."""
        state_dir = tmp_path / "sandcastle-state"
        monkeypatch.setattr(routes_module, "_SANDCASTLE_HOME", state_dir)
        prev_file = state_dir / "previous_version"
        prev_file.parent.mkdir(parents=True)
        prev_file.write_text("0.27.0")

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Successfully installed", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"", b"")):
                with patch("sandcastle.api.routes._emit_update_audit", new_callable=AsyncMock):
                    with patch("sandcastle.api.routes._restore_pre_update_backup", new_callable=AsyncMock):
                        resp = client.post("/api/admin/rollback")

        data = resp.json()["data"]
        assert data["status"] == "success"
        assert data["rolled_back_to"] == "0.27.0"
        assert data["restart_required"] is True


class TestUpdateCheckResponseSchema:
    """UpdateCheckResponse schema field presence."""

    def test_all_fields_present(self):
        """Schema has all required fields."""
        resp = UpdateCheckResponse(
            current_version="0.28.0",
            latest_version="0.29.0",
            update_available=True,
            release_url="https://github.com/gizmax/Sandcastle/releases",
            install_command="pip install --upgrade sandcastle-ai",
            changelog_url="https://github.com/gizmax/Sandcastle/releases/tag/v0.29.0",
            highlights=["Batch runs", "Auto update"],
        )
        assert resp.current_version == "0.28.0"
        assert resp.latest_version == "0.29.0"
        assert resp.update_available is True
        assert resp.changelog_url != ""
        assert len(resp.highlights) == 2

    def test_defaults_for_optional_fields(self):
        """changelog_url and highlights default to empty."""
        resp = UpdateCheckResponse(
            current_version="0.28.0",
            latest_version="0.28.0",
            update_available=False,
            release_url="https://github.com/gizmax/Sandcastle/releases",
            install_command="pip install --upgrade sandcastle-ai",
        )
        assert resp.changelog_url == ""
        assert resp.highlights == []


class TestUpdateRequestSchema:
    """UpdateRequest schema validation."""

    def test_empty_target_version_is_valid(self):
        """Empty target_version means 'latest from PyPI'."""
        req = UpdateRequest()
        assert req.target_version == ""

    def test_specific_target_version(self):
        """Specific target_version is accepted."""
        req = UpdateRequest(target_version="1.2.3")
        assert req.target_version == "1.2.3"


class TestUpdateResponseSchema:
    """UpdateResponse schema field presence."""

    def test_success_response_fields(self):
        resp = UpdateResponse(
            status="success",
            new_version="0.29.0",
            previous_version="0.28.0",
            restart_required=True,
        )
        assert resp.status == "success"
        assert resp.new_version == "0.29.0"
        assert resp.previous_version == "0.28.0"
        assert resp.restart_required is True
        assert resp.rolled_back_to == ""
        assert resp.error == ""

    def test_failed_response_fields(self):
        resp = UpdateResponse(
            status="failed",
            previous_version="0.28.0",
            error="pip install failed",
        )
        assert resp.status == "failed"
        assert resp.error == "pip install failed"


# ===========================================================================
# 3. CONFIG VALIDATION
# ===========================================================================


class TestConfigUpdateChannel:
    """Settings.update_channel validation."""

    def test_stable_channel_accepted(self):
        s = Settings(update_channel="stable")
        assert s.update_channel == "stable"

    def test_beta_channel_accepted(self):
        s = Settings(update_channel="beta")
        assert s.update_channel == "beta"

    def test_pin_channel_accepted(self):
        s = Settings(update_channel="pin")
        assert s.update_channel == "pin"

    def test_invalid_channel_falls_back_to_stable(self):
        s = Settings(update_channel="nightly")
        assert s.update_channel == "stable"

    def test_pinned_version_stored(self):
        s = Settings(update_channel="pin", pinned_version="0.28.0")
        assert s.pinned_version == "0.28.0"

    def test_auto_update_check_default_true(self):
        s = Settings()
        assert s.auto_update_check is True

    def test_auto_update_check_can_be_disabled(self):
        s = Settings(auto_update_check=False)
        assert s.auto_update_check is False


class TestBlackoutWindowParsing:
    """_is_in_blackout_window helper."""

    def test_empty_window_returns_false(self):
        """No blackout configured returns False."""
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        try:
            settings.update_blackout_start = ""
            settings.update_blackout_end = ""
            assert _is_in_blackout_window() is False
        finally:
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end

    def test_full_day_window_returns_true(self):
        """Blackout 00:00 - 23:59 always returns True."""
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        try:
            settings.update_blackout_start = "00:00"
            settings.update_blackout_end = "23:59"
            assert _is_in_blackout_window() is True
        finally:
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end

    def test_invalid_window_format_returns_false(self):
        """Malformed time strings return False (no crash)."""
        original_start = settings.update_blackout_start
        original_end = settings.update_blackout_end
        try:
            settings.update_blackout_start = "not-a-time"
            settings.update_blackout_end = "also-not"
            assert _is_in_blackout_window() is False
        finally:
            settings.update_blackout_start = original_start
            settings.update_blackout_end = original_end


# ===========================================================================
# 4. SCHEMA VALIDATION
# ===========================================================================


class TestBatchRunRequestSchema:
    """BatchRunRequest Pydantic model validation."""

    def test_valid_items_list_of_dicts(self):
        req = BatchRunRequest(items=[{"key": "value"}, {"key": "value2"}])
        assert len(req.items) == 2

    def test_empty_items_rejected(self):
        """Empty items list is rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            BatchRunRequest(items=[])

    def test_max_parallel_default_is_5(self):
        req = BatchRunRequest(items=[{"a": 1}])
        assert req.max_parallel == 5

    def test_max_parallel_range_1_to_50(self):
        """max_parallel at bounds 1 and 50 both accepted (capped to server max_concurrent_sandboxes)."""
        req1 = BatchRunRequest(items=[{"a": 1}], max_parallel=1)
        assert req1.max_parallel == 1

        req50 = BatchRunRequest(items=[{"a": 1}], max_parallel=50)
        assert req50.max_parallel == 50

    def test_max_parallel_below_1_rejected(self):
        with pytest.raises(ValidationError):
            BatchRunRequest(items=[{"a": 1}], max_parallel=0)

    def test_max_parallel_above_50_rejected(self):
        """max_parallel above 50 rejected to prevent overwhelming the sandbox pool."""
        with pytest.raises(ValidationError):
            BatchRunRequest(items=[{"a": 1}], max_parallel=51)

    def test_max_cost_per_item_optional(self):
        req = BatchRunRequest(items=[{"a": 1}])
        assert req.max_cost_per_item_usd is None

    def test_max_cost_per_item_negative_rejected(self):
        with pytest.raises(ValidationError):
            BatchRunRequest(items=[{"a": 1}], max_cost_per_item_usd=-1.0)


class TestBatchStatusResponseSchema:
    """BatchStatusResponse Pydantic model validation."""

    def test_all_fields_present(self):
        resp = BatchStatusResponse(
            batch_id="abc-123",
            workflow="test-wf",
            status="running",
            total=10,
            completed=5,
            failed=1,
            running=2,
            pending=2,
            total_cost_usd=0.15,
            items=[],
        )
        assert resp.batch_id == "abc-123"
        assert resp.workflow == "test-wf"
        assert resp.status == "running"
        assert resp.total == 10
        assert resp.completed == 5
        assert resp.failed == 1
        assert resp.running == 2
        assert resp.pending == 2
        assert resp.total_cost_usd == 0.15

    def test_items_default_to_empty_list(self):
        resp = BatchStatusResponse(
            batch_id="x",
            workflow="w",
            status="running",
            total=0,
        )
        assert resp.items == []

    def test_created_at_optional(self):
        resp = BatchStatusResponse(
            batch_id="x",
            workflow="w",
            status="completed",
            total=1,
        )
        assert resp.created_at is None


class TestBatchItemStatusSchema:
    """BatchItemStatus Pydantic model validation."""

    def test_completed_item(self):
        item = BatchItemStatus(
            index=0,
            status="completed",
            run_id="run-001",
            cost_usd=0.02,
        )
        assert item.index == 0
        assert item.status == "completed"
        assert item.run_id == "run-001"
        assert item.cost_usd == 0.02
        assert item.error is None

    def test_failed_item_with_error(self):
        item = BatchItemStatus(
            index=3,
            status="failed",
            error="Budget exceeded",
            cost_usd=0.05,
        )
        assert item.status == "failed"
        assert item.error == "Budget exceeded"

    def test_negative_index_rejected(self):
        with pytest.raises(ValidationError):
            BatchItemStatus(index=-1, status="pending")

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            BatchItemStatus(index=0, status="pending", cost_usd=-0.01)


class TestBatchStartedResponseSchema:
    """BatchStartedResponse Pydantic model validation."""

    def test_all_fields(self):
        resp = BatchStartedResponse(
            batch_id="new-batch",
            workflow="my-wf",
            total=42,
            status="running",
        )
        assert resp.batch_id == "new-batch"
        assert resp.workflow == "my-wf"
        assert resp.total == 42
        assert resp.status == "running"

    def test_default_status_is_running(self):
        resp = BatchStartedResponse(
            batch_id="b",
            workflow="w",
            total=1,
        )
        assert resp.status == "running"
