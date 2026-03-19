"""Tests for file upload support for workflow inputs."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pdf_bytes(size: int = 256) -> bytes:
    """Return minimal fake PDF bytes of a given size."""
    base = b"%PDF-1.4 fake pdf content for testing purposes"
    return base + b"\x00" * max(0, size - len(base))


def _upload_file(
    filename: str,
    content: bytes,
    content_type: str = "application/pdf",
) -> dict:
    """POST /api/upload and return the JSON response dict."""
    response = client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )
    return response


# ---------------------------------------------------------------------------
# Step 1 - Upload endpoint
# ---------------------------------------------------------------------------

class TestUploadEndpoint:
    """Tests for POST /api/upload."""

    def test_upload_valid_pdf(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        # Reload settings so DATA_DIR is picked up
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        response = _upload_file("report.pdf", _pdf_bytes())
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert "file_id" in data
        assert data["filename"] == "report.pdf"
        assert data["content_type"] == "application/pdf"
        assert data["size_bytes"] > 0
        assert data["storage"] == "local"
        # file_id should be a short hex string
        assert len(data["file_id"]) == 8
        assert all(c in "0123456789abcdef" for c in data["file_id"])

    def test_upload_valid_csv(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        content = b"name,value\nfoo,1\nbar,2\n"
        response = _upload_file("data.csv", content, "text/csv")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["filename"] == "data.csv"
        assert data["content_type"] == "text/csv"

    def test_upload_xlsx_allowed(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        content = b"PK fake xlsx content"
        response = _upload_file(
            "report.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["filename"] == "report.xlsx"

    def test_upload_docx_allowed(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        content = b"PK fake docx content"
        response = _upload_file(
            "document.docx",
            content,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["filename"] == "document.docx"

    def test_upload_invalid_extension(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        response = _upload_file("script.exe", b"MZ binary content", "application/octet-stream")
        assert response.status_code == 400
        error = response.json()["detail"]["error"]
        assert error["code"] == "BAD_REQUEST"
        assert ".exe" in error["message"]

    def test_upload_invalid_extension_py(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        response = _upload_file("evil.py", b"import os; os.system('rm -rf /')", "text/plain")
        assert response.status_code == 400

    def test_upload_oversized_file(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        # Local mode limit is 10 MB; create content just over that
        oversized = b"x" * (10 * 1024 * 1024 + 1)
        response = _upload_file("big.txt", oversized, "text/plain")
        assert response.status_code == 400
        error = response.json()["detail"]["error"]
        assert "too large" in error["message"].lower()

    def test_upload_no_filename(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        response = client.post(
            "/api/upload",
            files={"file": ("", io.BytesIO(b"content"), "text/plain")},
        )
        # FastAPI may return 422 (validation error) or 400 (our explicit check)
        assert response.status_code in (400, 422)

    def test_upload_file_id_format(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        response = _upload_file("test.txt", b"hello world", "text/plain")
        assert response.status_code == 200
        data = response.json()["data"]
        # file_id must be exactly 8 lowercase hex chars
        fid = data["file_id"]
        assert len(fid) == 8
        assert fid.isalnum()

    def test_upload_path_traversal_rejected(self, tmp_path, monkeypatch):
        from sandcastle import config
        config.settings.data_dir = str(tmp_path)

        # Attempt path traversal via filename
        response = _upload_file("../../etc/passwd", b"root:x:0:0", "text/plain")
        # Either rejected due to extension check or path traversal defense
        assert response.status_code in (400,)


# ---------------------------------------------------------------------------
# Step 3 - _resolve_file_input helper
# ---------------------------------------------------------------------------

class TestResolveFileInput:
    """Tests for the _resolve_file_input helper in executor.py."""

    @pytest.mark.asyncio
    async def test_passthrough_regular_string(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return []
            async def read(self, path):
                return None

        result = await _resolve_file_input("hello world", _MockStorage())
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_passthrough_non_upload_prefix(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return []
            async def read(self, path):
                return None

        result = await _resolve_file_input("@file:/some/path.txt", _MockStorage())
        assert result == "@file:/some/path.txt"

    @pytest.mark.asyncio
    async def test_resolve_text_upload(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return ["uploads/abc12345_data.csv"]

            async def read(self, path):
                if path == "uploads/abc12345_data.csv":
                    return "name,value\nfoo,1\nbar,2"
                return None

        result = await _resolve_file_input("@upload:abc12345", _MockStorage())
        assert "foo" in result
        assert "bar" in result

    @pytest.mark.asyncio
    async def test_resolve_binary_upload_returns_file_ref(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return ["uploads/abc12345_report.pdf"]

            async def read(self, path):
                return None  # should not be called for binary

        result = await _resolve_file_input("@upload:abc12345", _MockStorage())
        # Binary files return @file: reference, not content
        assert result.startswith("@file:")

    @pytest.mark.asyncio
    async def test_resolve_missing_file_returns_original(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return []

            async def read(self, path):
                return None

        original = "@upload:notfound1"
        result = await _resolve_file_input(original, _MockStorage())
        assert result == original

    @pytest.mark.asyncio
    async def test_passthrough_non_string(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return []
            async def read(self, path):
                return None

        result = await _resolve_file_input(42, _MockStorage())  # type: ignore[arg-type]
        assert result == 42

    @pytest.mark.asyncio
    async def test_resolve_json_text_file(self):
        from sandcastle.engine.executor import _resolve_file_input

        class _MockStorage:
            async def list(self, prefix):
                return ["uploads/abc12345_data.json"]

            async def read(self, path):
                return '{"key": "value", "items": [1, 2, 3]}'

        result = await _resolve_file_input("@upload:abc12345", _MockStorage())
        assert '"key"' in result
        assert '"value"' in result


# ---------------------------------------------------------------------------
# Step 2 - input_schema with type: file validates correctly (dag.py)
# ---------------------------------------------------------------------------

class TestInputSchemaFileType:
    """Tests that input_schema with type: file is handled correctly by dag.py."""

    def test_parse_workflow_with_file_input(self):
        from sandcastle.engine.dag import parse_yaml_string

        yaml_content = """
name: file-workflow
description: Workflow with file input
steps:
  - id: process
    prompt: "Analyze this document: {input.document}"
    model: haiku
input_schema:
  properties:
    document:
      type: file
      description: "PDF document to analyze"
      accept: ".pdf,.csv,.xlsx"
  required:
    - document
"""
        workflow = parse_yaml_string(yaml_content)
        assert workflow.input_schema is not None
        props = workflow.input_schema.get("properties", {})
        assert "document" in props
        assert props["document"]["type"] == "file"
        assert props["document"]["accept"] == ".pdf,.csv,.xlsx"

    def test_validate_workflow_with_file_input(self):
        from sandcastle.engine.dag import parse_yaml_string, validate

        yaml_content = """
name: file-workflow
description: Workflow with file input
steps:
  - id: process
    prompt: "Analyze this document: {input.document}"
    model: haiku
input_schema:
  properties:
    document:
      type: file
      description: "Document to analyze"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        # There should be no errors about the file type in input_schema
        # (input_schema validation is permissive - it's just a dict)
        input_related = [e for e in errors if "input_schema" in e.lower() or "file" in e.lower()]
        assert not input_related, f"Unexpected input_schema errors: {input_related}"


# ---------------------------------------------------------------------------
# S3 upload (mocked)
# ---------------------------------------------------------------------------

class TestS3Upload:
    """Tests for S3 upload path (mocked aioboto3)."""

    def test_upload_routes_to_s3_when_configured(self, tmp_path, monkeypatch):
        from sandcastle import config

        monkeypatch.setattr(config.settings, "storage_backend", "s3")
        monkeypatch.setattr(config.settings, "storage_bucket", "test-bucket")
        monkeypatch.setattr(config.settings, "storage_endpoint", None)
        monkeypatch.setattr(config.settings, "aws_access_key_id", "test")
        monkeypatch.setattr(config.settings, "aws_secret_access_key", "test")
        monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))

        mock_s3 = AsyncMock()
        mock_s3.put_object = AsyncMock()
        mock_s3.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_s3.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.client = MagicMock(return_value=mock_s3)

        import aioboto3 as _aioboto3

        # aioboto3 is imported inside the route handler; patch it at module level
        with patch.object(_aioboto3, "Session", return_value=mock_session):
            response = _upload_file("report.pdf", _pdf_bytes())

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["storage"] == "s3"
        assert data["url"] is not None
        assert "s3://" in data["url"]
        assert "file_id" in data
        assert len(data["file_id"]) == 8

        # Restore
        monkeypatch.setattr(config.settings, "storage_backend", "local")
