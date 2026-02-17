"""Tests for tenant isolation, admin roles, sandbox root, and browse guard."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sandcastle.config import settings
from sandcastle.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _restore_settings():
    """Save and restore settings modified by tests."""
    original = {
        "auth_required": settings.auth_required,
        "sandbox_root": settings.sandbox_root,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)


# ---------------------------------------------------------------------------
# Tests: is_admin helper
# ---------------------------------------------------------------------------


class TestIsAdmin:
    def test_admin_when_auth_disabled(self):
        """Everyone is admin when auth is not required."""
        from sandcastle.api.auth import is_admin

        settings.auth_required = False
        req = MagicMock()
        req.state.tenant_id = "tenant-123"
        assert is_admin(req) is True

    def test_admin_when_tenant_id_is_none(self):
        """Admin key (tenant_id=None) is admin when auth is enabled."""
        from sandcastle.api.auth import is_admin

        settings.auth_required = True
        req = MagicMock()
        req.state.tenant_id = None
        assert is_admin(req) is True

    def test_not_admin_when_tenant_key(self):
        """Tenant key is not admin when auth is enabled."""
        from sandcastle.api.auth import is_admin

        settings.auth_required = True
        req = MagicMock()
        req.state.tenant_id = "acme-corp"
        assert is_admin(req) is False


# ---------------------------------------------------------------------------
# Tests: _require_admin uses is_admin
# ---------------------------------------------------------------------------


class TestRequireAdmin:
    def test_raises_403_for_tenant(self):
        """_require_admin blocks tenant keys when auth is enabled."""
        from sandcastle.api.routes import _require_admin

        settings.auth_required = True
        req = MagicMock()
        with patch("sandcastle.api.routes.is_admin", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                _require_admin(req)
        assert exc_info.value.status_code == 403

    def test_passes_for_admin(self):
        """_require_admin passes for admin key."""
        from sandcastle.api.routes import _require_admin

        settings.auth_required = True
        req = MagicMock()
        with patch("sandcastle.api.routes.is_admin", return_value=True):
            _require_admin(req)  # should not raise


# ---------------------------------------------------------------------------
# Tests: Browse guard - local mode only
# ---------------------------------------------------------------------------


class TestBrowseGuard:
    def test_browse_returns_403_in_production_mode(self):
        """Browse endpoint returns 403 when not in local mode."""
        prop = property(lambda self: False)
        with patch.object(type(settings), "is_local_mode", new_callable=lambda: prop):
            response = client.get("/api/browse", params={"path": "~"})
        assert response.status_code == 403

    def test_browse_works_in_local_mode(self):
        """Browse endpoint works in local mode for valid paths."""
        settings.sandbox_root = ""
        response = client.get("/api/browse", params={"path": "/tmp"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert "entries" in data
        assert "current" in data


# ---------------------------------------------------------------------------
# Tests: Browse sandbox root
# ---------------------------------------------------------------------------


class TestBrowseSandboxRoot:
    def test_browse_blocks_path_outside_sandbox(self):
        """Browse with sandbox_root set should block paths outside the root."""
        with tempfile.TemporaryDirectory() as sandbox:
            settings.sandbox_root = sandbox
            response = client.get("/api/browse", params={"path": "/tmp"})
            assert response.status_code == 403
            assert "outside sandbox root" in response.json()["detail"]

    def test_browse_allows_path_inside_sandbox(self):
        """Browse with sandbox_root set should allow paths inside the root."""
        with tempfile.TemporaryDirectory() as sandbox:
            # Create a subdirectory inside sandbox
            subdir = Path(sandbox) / "subdir"
            subdir.mkdir()
            settings.sandbox_root = sandbox
            response = client.get("/api/browse", params={"path": str(subdir)})
            assert response.status_code == 200

    def test_browse_allows_sandbox_root_itself(self):
        """Browse should allow browsing the sandbox root directory itself."""
        with tempfile.TemporaryDirectory() as sandbox:
            settings.sandbox_root = sandbox
            response = client.get("/api/browse", params={"path": sandbox})
            assert response.status_code == 200

    def test_browse_no_restriction_when_sandbox_root_empty(self):
        """Browse should not restrict paths when sandbox_root is empty."""
        settings.sandbox_root = ""
        response = client.get("/api/browse", params={"path": "/tmp"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests: CSV output sandbox root
# ---------------------------------------------------------------------------


class TestCsvSandboxRoot:
    def test_csv_output_skips_outside_sandbox(self):
        """_write_csv_output should skip writing when directory is outside sandbox."""
        from sandcastle.engine.executor import _write_csv_output

        with tempfile.TemporaryDirectory() as sandbox:
            settings.sandbox_root = sandbox
            step = MagicMock()
            step.csv_output.directory = "/tmp/evil"
            step.csv_output.filename = "test"
            step.id = "test-step"

            _write_csv_output(step, {"key": "value"}, "run-123")

            # The file should NOT have been created
            assert not Path("/tmp/evil/test.csv").exists()

    def test_csv_output_writes_inside_sandbox(self):
        """_write_csv_output should write when directory is inside sandbox."""
        from sandcastle.engine.executor import _write_csv_output

        with tempfile.TemporaryDirectory() as sandbox:
            settings.sandbox_root = sandbox
            output_dir = Path(sandbox) / "output"
            step = MagicMock()
            step.csv_output.directory = str(output_dir)
            step.csv_output.filename = "test"
            step.csv_output.mode = "new_file"
            step.id = "test-step"

            _write_csv_output(step, {"key": "value"}, "run-123")

            # The file should have been created inside sandbox
            csv_files = list(output_dir.glob("*.csv"))
            assert len(csv_files) == 1

    def test_csv_output_no_restriction_when_sandbox_empty(self):
        """_write_csv_output should write anywhere when sandbox_root is empty."""
        from sandcastle.engine.executor import _write_csv_output

        settings.sandbox_root = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            step = MagicMock()
            step.csv_output.directory = tmpdir
            step.csv_output.filename = "test"
            step.csv_output.mode = "new_file"
            step.id = "test-step"

            _write_csv_output(step, {"key": "value"}, "run-123")

            csv_files = list(Path(tmpdir).glob("*.csv"))
            assert len(csv_files) == 1


# ---------------------------------------------------------------------------
# Tests: SSE events tenant filtering
# ---------------------------------------------------------------------------


class TestEventsIsolation:
    def test_tenant_id_extracted_correctly(self):
        """get_tenant_id returns the correct tenant scope from request."""
        from sandcastle.api.auth import get_tenant_id

        req = MagicMock()
        req.state.tenant_id = "acme-corp"
        assert get_tenant_id(req) == "acme-corp"

        req.state.tenant_id = None
        assert get_tenant_id(req) is None

    def test_tenant_id_none_when_no_tenant_attr(self):
        """get_tenant_id returns None when request.state has no tenant_id."""
        from sandcastle.api.auth import get_tenant_id

        req = MagicMock()
        # Simulate state without tenant_id attribute
        req.state = MagicMock(spec=[])
        assert get_tenant_id(req) is None
