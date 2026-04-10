"""Tests for GLM-OCR integration - 3rd OCR engine via Ollama.

Covers: ParseConfig validation, availability check, parsing, auto-mode
fallback logic, PDF-to-image conversion, and edge cases.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. ParseConfig validation
# ---------------------------------------------------------------------------

class TestParseConfigValidation:
    """Validate that ocr_engine='glm-ocr' passes DAG validation."""

    def test_glm_ocr_is_valid_engine(self):
        """ParseConfig with ocr_engine='glm-ocr' should be accepted."""
        from sandcastle.engine.dag import ParseConfig

        cfg = ParseConfig(ocr_engine="glm-ocr")
        assert cfg.ocr_engine == "glm-ocr"

    def test_invalid_engine_still_rejected(self):
        """An unknown engine name should still fail validation."""
        from sandcastle.engine.dag import ParseConfig, validate, WorkflowDefinition, StepDefinition

        step = StepDefinition(
            id="bad_parse",
            type="parse",
            prompt="/tmp/test.pdf",
            parse_config=ParseConfig(ocr_engine="nonexistent"),
        )
        wf = WorkflowDefinition(
            name="test",
            description="test workflow",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[step],
        )
        errors = validate(wf)
        assert any("nonexistent" in e for e in errors)

    def test_dag_validation_accepts_glm_ocr(self):
        """Full DAG validation should accept glm-ocr as ocr_engine."""
        from sandcastle.engine.dag import ParseConfig, validate, WorkflowDefinition, StepDefinition

        step = StepDefinition(
            id="parse_doc",
            type="parse",
            prompt="/tmp/test.pdf",
            parse_config=ParseConfig(ocr_engine="glm-ocr"),
        )
        wf = WorkflowDefinition(
            name="test",
            description="test workflow",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[step],
        )
        errors = validate(wf)
        # Should not contain any error about ocr_engine
        assert not any("ocr_engine" in e for e in errors)


# ---------------------------------------------------------------------------
# 2. _is_glm_ocr_available()
# ---------------------------------------------------------------------------

class TestIsGlmOcrAvailable:
    """Test the Ollama availability check."""

    def test_returns_false_when_ollama_not_running(self):
        """Should return False when Ollama is unreachable."""
        from sandcastle.engine.docparse import _is_glm_ocr_available

        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = Exception("Connection refused")

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = _is_glm_ocr_available()
        assert result is False

    def test_returns_false_when_httpx_import_fails(self):
        """Should return False when httpx is not installed."""
        from sandcastle.engine.docparse import _is_glm_ocr_available

        with patch.dict(sys.modules, {"httpx": None}):
            result = _is_glm_ocr_available()
        assert result is False

    def test_returns_true_when_model_present(self):
        """Should return True when Ollama has glm-ocr model."""
        from sandcastle.engine.docparse import _is_glm_ocr_available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:latest"},
                {"name": "glm-ocr:latest"},
            ]
        }

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_response

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = _is_glm_ocr_available()
        assert result is True

    def test_returns_false_when_model_absent(self):
        """Should return False when Ollama runs but glm-ocr is not pulled."""
        from sandcastle.engine.docparse import _is_glm_ocr_available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:latest"},
                {"name": "mistral:7b"},
            ]
        }

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_response

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = _is_glm_ocr_available()
        assert result is False

    def test_respects_ollama_host_env(self):
        """Should read OLLAMA_HOST from environment."""
        from sandcastle.engine.docparse import _is_glm_ocr_available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "glm-ocr:latest"}]}

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_response

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            with patch.dict("os.environ", {"OLLAMA_HOST": "http://custom:9999"}):
                _is_glm_ocr_available()
            mock_httpx.get.assert_called_with(
                "http://custom:9999/api/tags", timeout=3
            )


# ---------------------------------------------------------------------------
# 3. _parse_with_glm_ocr()
# ---------------------------------------------------------------------------

class TestParseWithGlmOcr:
    """Test the GLM-OCR parsing function with mocked dependencies."""

    def _make_mock_doc(self, num_pages=2):
        """Create a mock pymupdf document."""
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=num_pages)

        pages = []
        for _ in range(num_pages):
            mock_page = MagicMock()
            mock_pix = MagicMock()
            mock_pix.tobytes.return_value = b"fake-png-bytes"
            mock_page.get_pixmap.return_value = mock_pix
            pages.append(mock_page)

        mock_doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
        mock_doc.close = MagicMock()
        return mock_doc

    def test_successful_parse(self, tmp_path):
        """Should parse PDF pages through Ollama and return ParseResult."""
        from sandcastle.engine.docparse import _parse_with_glm_ocr

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = self._make_mock_doc(num_pages=2)
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Extracted text from page"}
        }
        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_response

        with patch.dict(sys.modules, {"fitz": mock_fitz, "httpx": mock_httpx}):
            result = _parse_with_glm_ocr(str(pdf_file), "markdown")

        assert result.pages == 2
        assert result.metadata["parse_method"] == "glm-ocr"
        assert result.metadata["model"] == "glm-ocr"
        assert result.metadata["accuracy_benchmark"] == "94.62%"
        assert "Extracted text from page" in result.text

    def test_parse_result_metadata(self, tmp_path):
        """ParseResult should contain correct metadata fields."""
        from sandcastle.engine.docparse import _parse_with_glm_ocr

        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = self._make_mock_doc(num_pages=1)
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": {"content": "Page 1 text"}}
        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_response

        with patch.dict(sys.modules, {"fitz": mock_fitz, "httpx": mock_httpx}):
            result = _parse_with_glm_ocr(str(pdf_file), "text")

        assert result.format == "text"
        assert result.metadata["filename"] == "report.pdf"
        assert result.metadata["content_type"] == "application/pdf"

    def test_empty_document(self, tmp_path):
        """Should handle a document with zero extractable pages."""
        from sandcastle.engine.docparse import _parse_with_glm_ocr

        pdf_file = tmp_path / "empty.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=0)
        mock_doc.__getitem__ = MagicMock(side_effect=IndexError)
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            result = _parse_with_glm_ocr(str(pdf_file), "markdown")

        assert result.pages == 0
        assert result.text == ""
        assert result.metadata["parse_method"] == "glm-ocr"

    def test_page_range_support(self, tmp_path):
        """Should only process specified page ranges."""
        from sandcastle.engine.docparse import _parse_with_glm_ocr

        pdf_file = tmp_path / "multi.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = self._make_mock_doc(num_pages=10)
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": {"content": "page text"}}
        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_response

        with patch.dict(sys.modules, {"fitz": mock_fitz, "httpx": mock_httpx}):
            result = _parse_with_glm_ocr(str(pdf_file), "markdown", pages="1-3")

        # Pages "1-3" should produce 3 pages (0-indexed: 0, 1, 2)
        assert result.pages == 3

    def test_ollama_error_response(self, tmp_path):
        """Should handle non-200 responses from Ollama gracefully."""
        from sandcastle.engine.docparse import _parse_with_glm_ocr

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = self._make_mock_doc(num_pages=1)
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "model not found"}
        mock_httpx = MagicMock()
        mock_httpx.post.return_value = mock_response

        with patch.dict(sys.modules, {"fitz": mock_fitz, "httpx": mock_httpx}):
            result = _parse_with_glm_ocr(str(pdf_file), "markdown")

        # Should still return a result, just with empty text for failed pages
        assert result.pages == 1
        assert result.text == ""

    def test_page_separator_format(self, tmp_path):
        """Pages should be joined with markdown separator."""
        from sandcastle.engine.docparse import _parse_with_glm_ocr

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = self._make_mock_doc(num_pages=2)
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        call_count = [0]
        def make_response(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"message": {"content": f"Page {call_count[0]}"}}
            return resp

        mock_httpx = MagicMock()
        mock_httpx.post.side_effect = make_response

        with patch.dict(sys.modules, {"fitz": mock_fitz, "httpx": mock_httpx}):
            result = _parse_with_glm_ocr(str(pdf_file), "markdown")

        assert "\n\n---\n\n" in result.text
        assert "Page 1" in result.text
        assert "Page 2" in result.text


# ---------------------------------------------------------------------------
# 4. _pdf_to_images()
# ---------------------------------------------------------------------------

class TestPdfToImages:
    """Test PDF-to-image conversion helper."""

    def test_converts_all_pages(self, tmp_path):
        """Should convert all pages when no page range specified."""
        from sandcastle.engine.docparse import _pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=3)
        pages_list = []
        for _ in range(3):
            page = MagicMock()
            pix = MagicMock()
            pix.tobytes.return_value = b"png-data"
            page.get_pixmap.return_value = pix
            pages_list.append(page)
        mock_doc.__getitem__ = MagicMock(side_effect=lambda i: pages_list[i])
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            images = _pdf_to_images(str(pdf_file))

        assert len(images) == 3
        assert all(img == b"png-data" for img in images)

    def test_respects_page_range(self, tmp_path):
        """Should only convert specified pages."""
        from sandcastle.engine.docparse import _pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=5)
        pages_list = []
        for i in range(5):
            page = MagicMock()
            pix = MagicMock()
            pix.tobytes.return_value = f"png-page-{i}".encode()
            page.get_pixmap.return_value = pix
            pages_list.append(page)
        mock_doc.__getitem__ = MagicMock(side_effect=lambda i: pages_list[i])
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            images = _pdf_to_images(str(pdf_file), pages="2-3")

        # Pages "2-3" = 0-indexed [1, 2] = 2 images
        assert len(images) == 2

    def test_uses_300_dpi(self, tmp_path):
        """Should render pages at 300 DPI for OCR accuracy."""
        from sandcastle.engine.docparse import _pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"png"
        mock_page.get_pixmap.return_value = mock_pix

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            _pdf_to_images(str(pdf_file))

        mock_page.get_pixmap.assert_called_once_with(dpi=300)


# ---------------------------------------------------------------------------
# 5. Auto-mode fallback logic
# ---------------------------------------------------------------------------

class TestAutoModeFallback:
    """Test auto engine selection with GLM-OCR in the fallback chain."""

    def test_auto_prefers_glm_ocr_over_chandra(self, tmp_path):
        """When pymupdf returns sparse text and GLM-OCR is available, use GLM-OCR."""
        from sandcastle.engine.docparse import _parse_pdf_dispatch, ParseResult

        pdf_file = tmp_path / "scanned.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        sparse_result = ParseResult(text="", format="markdown", pages=1, metadata={})
        glm_result = ParseResult(
            text="GLM extracted text",
            format="markdown",
            pages=1,
            metadata={"parse_method": "glm-ocr"},
        )

        with patch(
            "sandcastle.engine.docparse._parse_pdf", return_value=sparse_result
        ), patch(
            "sandcastle.engine.docparse._is_glm_ocr_available", return_value=True
        ), patch(
            "sandcastle.engine.docparse._parse_with_glm_ocr", return_value=glm_result
        ) as mock_glm:
            result = _parse_pdf_dispatch(
                Path(pdf_file), "markdown", None, False, "auto", None
            )

        mock_glm.assert_called_once()
        assert result.metadata["parse_method"] == "glm-ocr"

    def test_auto_falls_back_to_chandra_when_glm_unavailable(self, tmp_path):
        """When GLM-OCR is not available, should fall back to Chandra."""
        from sandcastle.engine.docparse import _parse_pdf_dispatch, ParseResult

        pdf_file = tmp_path / "scanned.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        sparse_result = ParseResult(text="", format="markdown", pages=1, metadata={})
        chandra_result = ParseResult(
            text="Chandra extracted text",
            format="markdown",
            pages=1,
            metadata={"parse_method": "chandra"},
        )

        with patch(
            "sandcastle.engine.docparse._parse_pdf", return_value=sparse_result
        ), patch(
            "sandcastle.engine.docparse._is_glm_ocr_available", return_value=False
        ), patch(
            "sandcastle.engine.docparse._parse_with_chandra", return_value=chandra_result
        ) as mock_chandra:
            result = _parse_pdf_dispatch(
                Path(pdf_file), "markdown", None, False, "auto", None
            )

        mock_chandra.assert_called_once()
        assert result.metadata["parse_method"] == "chandra"

    def test_auto_keeps_pymupdf_when_text_sufficient(self, tmp_path):
        """When pymupdf extracts enough text, skip OCR entirely."""
        from sandcastle.engine.docparse import _parse_pdf_dispatch, ParseResult

        pdf_file = tmp_path / "digital.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        good_result = ParseResult(
            text="A" * 200,  # plenty of text
            format="markdown",
            pages=1,
            metadata={"parse_method": "pymupdf"},
        )

        with patch(
            "sandcastle.engine.docparse._parse_pdf", return_value=good_result
        ), patch(
            "sandcastle.engine.docparse._is_glm_ocr_available"
        ) as mock_avail:
            result = _parse_pdf_dispatch(
                Path(pdf_file), "markdown", None, False, "auto", None
            )

        # Should not even check GLM-OCR availability
        mock_avail.assert_not_called()
        assert result.text == "A" * 200

    def test_auto_survives_glm_ocr_failure(self, tmp_path):
        """If GLM-OCR fails, should fall back to Chandra."""
        from sandcastle.engine.docparse import _parse_pdf_dispatch, ParseResult

        pdf_file = tmp_path / "scanned.pdf"
        pdf_file.write_bytes(b"fake-pdf")

        sparse_result = ParseResult(text="", format="markdown", pages=1, metadata={})
        chandra_result = ParseResult(
            text="Chandra text",
            format="markdown",
            pages=1,
            metadata={"parse_method": "chandra"},
        )

        with patch(
            "sandcastle.engine.docparse._parse_pdf", return_value=sparse_result
        ), patch(
            "sandcastle.engine.docparse._is_glm_ocr_available", return_value=True
        ), patch(
            "sandcastle.engine.docparse._parse_with_glm_ocr",
            side_effect=RuntimeError("Ollama crashed"),
        ), patch(
            "sandcastle.engine.docparse._parse_with_chandra", return_value=chandra_result
        ):
            result = _parse_pdf_dispatch(
                Path(pdf_file), "markdown", None, False, "auto", None
            )

        assert result.metadata["parse_method"] == "chandra"


# ---------------------------------------------------------------------------
# 6. Config - ollama_host setting
# ---------------------------------------------------------------------------

class TestConfigOllamaHost:
    """Verify ollama_host is present in Settings."""

    def test_ollama_host_default(self):
        """Settings should have ollama_host with default localhost:11434."""
        from sandcastle.config import Settings

        s = Settings(anthropic_api_key="test")
        assert s.ollama_host == "http://localhost:11434"

    def test_ollama_host_custom(self):
        """Settings should accept custom ollama_host."""
        from sandcastle.config import Settings

        s = Settings(anthropic_api_key="test", ollama_host="http://gpu-box:11434")
        assert s.ollama_host == "http://gpu-box:11434"


# ---------------------------------------------------------------------------
# 7. Direct engine dispatch
# ---------------------------------------------------------------------------

class TestDirectEngineDispatch:
    """Test that ocr_engine='glm-ocr' directly calls _parse_with_glm_ocr."""

    def test_dispatch_glm_ocr_engine(self, tmp_path):
        """ocr_engine='glm-ocr' should route directly to _parse_with_glm_ocr."""
        from sandcastle.engine.docparse import _parse_pdf_dispatch, ParseResult

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake")

        glm_result = ParseResult(
            text="direct glm",
            format="markdown",
            pages=1,
            metadata={"parse_method": "glm-ocr"},
        )

        with patch(
            "sandcastle.engine.docparse._parse_with_glm_ocr", return_value=glm_result
        ) as mock_glm, patch(
            "sandcastle.engine.docparse._parse_pdf"
        ) as mock_pymupdf:
            result = _parse_pdf_dispatch(
                Path(pdf_file), "markdown", None, False, "glm-ocr", None
            )

        mock_glm.assert_called_once_with(str(pdf_file), "markdown", None)
        mock_pymupdf.assert_not_called()
        assert result.metadata["parse_method"] == "glm-ocr"

    def test_dispatch_does_not_affect_chandra(self, tmp_path):
        """ocr_engine='chandra' should still route to _parse_with_chandra."""
        from sandcastle.engine.docparse import _parse_pdf_dispatch, ParseResult

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake")

        chandra_result = ParseResult(
            text="chandra text",
            format="markdown",
            pages=1,
            metadata={"parse_method": "chandra"},
        )

        with patch(
            "sandcastle.engine.docparse._parse_with_chandra", return_value=chandra_result
        ) as mock_chandra, patch(
            "sandcastle.engine.docparse._parse_with_glm_ocr"
        ) as mock_glm:
            result = _parse_pdf_dispatch(
                Path(pdf_file), "markdown", None, False, "chandra", None
            )

        mock_chandra.assert_called_once()
        mock_glm.assert_not_called()
        assert result.metadata["parse_method"] == "chandra"
