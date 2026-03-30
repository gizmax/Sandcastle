"""Tests for Chandra OCR integration in the parse step (v0.28).

Covers ParseConfig ocr_engine/languages fields, _parse_with_chandra(),
auto-mode fallback logic, and validation of the new fields.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.dag import ParseConfig, parse_yaml_string, validate
from sandcastle.engine.docparse import (
    ParseResult,
    _parse_with_chandra,
    parse_document,
)
from sandcastle.engine.executor import RunContext, _execute_parse_step


def _make_context(step_outputs=None, run_input=None) -> RunContext:
    return RunContext(
        run_id="test-run-id",
        input=run_input or {},
        step_outputs=step_outputs or {},
    )


def _make_chandra_module(return_text: str = "Chandra text") -> types.ModuleType:
    """Create a fake chandra_ocr module with an ocr() function."""
    mod = types.ModuleType("chandra_ocr")
    mod.ocr = MagicMock(return_value=return_text)
    return mod


# ---------------------------------------------------------------------------
# ParseConfig new fields
# ---------------------------------------------------------------------------


def test_parse_config_ocr_engine_default():
    """ParseConfig.ocr_engine defaults to 'auto'."""
    cfg = ParseConfig()
    assert cfg.ocr_engine == "auto"


def test_parse_config_ocr_engine_chandra():
    """ParseConfig accepts ocr_engine='chandra'."""
    cfg = ParseConfig(ocr_engine="chandra")
    assert cfg.ocr_engine == "chandra"


def test_parse_config_languages_default():
    """ParseConfig.languages defaults to None."""
    cfg = ParseConfig()
    assert cfg.languages is None


def test_parse_config_languages_set():
    """ParseConfig accepts a list of language codes."""
    cfg = ParseConfig(languages=["cs", "en"])
    assert cfg.languages == ["cs", "en"]


# ---------------------------------------------------------------------------
# YAML parsing of new fields
# ---------------------------------------------------------------------------


def test_yaml_parse_config_ocr_engine_chandra():
    """parse_config.ocr_engine is parsed from YAML."""
    yaml = """
name: test-chandra
steps:
  - id: ocr_doc
    type: parse
    prompt: "@file:/tmp/scan.pdf"
    parse_config:
      output: markdown
      ocr_engine: chandra
      languages:
        - cs
        - en
"""
    wf = parse_yaml_string(yaml)
    step = wf.steps[0]
    assert step.parse_config is not None
    assert step.parse_config.ocr_engine == "chandra"
    assert step.parse_config.languages == ["cs", "en"]


def test_yaml_parse_config_ocr_engine_default():
    """parse_config without ocr_engine defaults to 'auto'."""
    yaml = """
name: test-auto
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.pdf"
    parse_config:
      output: text
"""
    wf = parse_yaml_string(yaml)
    step = wf.steps[0]
    assert step.parse_config.ocr_engine == "auto"
    assert step.parse_config.languages is None


# ---------------------------------------------------------------------------
# Validation of ocr_engine
# ---------------------------------------------------------------------------


def test_validate_ocr_engine_invalid():
    """validate() returns error for invalid ocr_engine value."""
    yaml = """
name: test-invalid-engine
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.pdf"
    parse_config:
      ocr_engine: tesseract
"""
    wf = parse_yaml_string(yaml)
    errors = validate(wf)
    engine_errors = [e for e in errors if "ocr_engine" in e]
    assert len(engine_errors) == 1
    assert "tesseract" in engine_errors[0]


def test_validate_ocr_engine_valid_values():
    """validate() accepts all valid ocr_engine values without ocr_engine errors."""
    for engine in ("auto", "pymupdf", "chandra"):
        yaml = f"""
name: test-engine-{engine}
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.pdf"
    parse_config:
      ocr_engine: {engine}
"""
        wf = parse_yaml_string(yaml)
        errors = validate(wf)
        engine_errors = [e for e in errors if "ocr_engine" in e]
        assert not engine_errors, f"Unexpected validation error for engine={engine}: {engine_errors}"


# ---------------------------------------------------------------------------
# _parse_with_chandra - import error handling
# ---------------------------------------------------------------------------


def test_parse_with_chandra_not_installed(tmp_path):
    """_parse_with_chandra raises ImportError when chandra-ocr is not installed."""
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    with patch.dict("sys.modules", {"chandra_ocr": None}):
        with pytest.raises(ImportError, match="chandra-ocr"):
            _parse_with_chandra(str(f))


# ---------------------------------------------------------------------------
# _parse_with_chandra - mocked chandra
# ---------------------------------------------------------------------------


def test_parse_with_chandra_mock_success(tmp_path):
    """_parse_with_chandra returns ParseResult with chandra metadata when mocked."""
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    fake_mod = _make_chandra_module("# Extracted Title\n\nSome scanned text.")

    import sys
    sys.modules["chandra_ocr"] = fake_mod
    try:
        result = _parse_with_chandra(str(f), output_format="markdown", languages=["cs", "en"])
    finally:
        del sys.modules["chandra_ocr"]

    assert isinstance(result, ParseResult)
    assert "Extracted Title" in result.text
    assert result.format == "markdown"
    assert result.metadata["parse_method"] == "chandra"
    assert result.metadata["languages"] == ["cs", "en"]
    fake_mod.ocr.assert_called_once()


def test_parse_with_chandra_passes_kwargs(tmp_path):
    """_parse_with_chandra passes output_format, languages and pages to chandra_ocr.ocr."""
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    fake_mod = _make_chandra_module("text content")

    import sys
    sys.modules["chandra_ocr"] = fake_mod
    try:
        _parse_with_chandra(str(f), output_format="text", pages="1-3", languages=["en"])
    finally:
        del sys.modules["chandra_ocr"]

    call_kwargs = fake_mod.ocr.call_args
    assert call_kwargs[1]["output_format"] == "text"
    assert call_kwargs[1]["languages"] == ["en"]
    assert call_kwargs[1]["pages"] == "1-3"


# ---------------------------------------------------------------------------
# Auto mode - fallback logic
# ---------------------------------------------------------------------------


def test_auto_mode_uses_pymupdf_when_text_present(tmp_path):
    """Auto mode keeps pymupdf result when it extracts sufficient text."""
    f = tmp_path / "text.pdf"
    f.write_bytes(b"%PDF-1.4")

    # Mock pymupdf to return plenty of text
    mock_page = MagicMock()
    mock_page.get_text.return_value = "A" * 200
    mock_page.get_images.return_value = []

    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc
    mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

    with patch("sandcastle.engine.docparse._parse_pdf") as mock_parse_pdf:
        mock_parse_pdf.return_value = ParseResult(
            text="A" * 200,
            format="text",
            pages=1,
            metadata={"parse_method": "pymupdf", "filename": "text.pdf"},
        )
        result = parse_document(str(f), output_format="text", ocr_engine="auto")

    assert result.metadata["parse_method"] == "pymupdf"


def test_auto_mode_falls_back_to_chandra_on_empty(tmp_path):
    """Auto mode falls back to Chandra when pymupdf returns very little text."""
    f = tmp_path / "scanned.pdf"
    f.write_bytes(b"%PDF-1.4")

    fake_chandra = _make_chandra_module("Chandra extracted this text from the scanned PDF.")

    import sys
    sys.modules["chandra_ocr"] = fake_chandra
    try:
        with patch("sandcastle.engine.docparse._parse_pdf") as mock_parse_pdf:
            mock_parse_pdf.return_value = ParseResult(
                text="",
                format="markdown",
                pages=1,
                metadata={"parse_method": "pymupdf", "filename": "scanned.pdf"},
            )
            result = parse_document(str(f), output_format="markdown", ocr_engine="auto")
    finally:
        del sys.modules["chandra_ocr"]

    assert result.metadata["parse_method"] == "chandra"
    assert "Chandra extracted" in result.text


# ---------------------------------------------------------------------------
# Non-PDF files always use existing parsers
# ---------------------------------------------------------------------------


def test_docx_ignores_ocr_engine_setting(tmp_path):
    """DOCX files always use the existing parser regardless of ocr_engine."""
    import zipfile

    docx_path = tmp_path / "doc.docx"
    content_types = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'</Types>'
    )
    with zipfile.ZipFile(str(docx_path), "w") as z:
        z.writestr("[Content_Types].xml", content_types)

    mock_para = MagicMock()
    mock_para.text = "Hello Document"
    mock_para.style.name = "Normal"

    mock_doc = MagicMock()
    mock_doc.paragraphs = [mock_para]
    mock_doc.tables = []

    mock_docx_module = MagicMock()
    mock_docx_module.Document.return_value = mock_doc

    with patch.dict("sys.modules", {"docx": mock_docx_module}):
        result = parse_document(str(docx_path), output_format="text", ocr_engine="chandra")

    assert "Hello Document" in result.text
    assert result.metadata.get("parse_method") != "chandra"


def test_csv_ignores_ocr_engine_setting(tmp_path):
    """CSV files always use the existing parser regardless of ocr_engine."""
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30")

    result = parse_document(str(f), output_format="markdown", ocr_engine="chandra")
    assert "Alice" in result.text
    assert result.metadata.get("parse_method") != "chandra"


def test_txt_ignores_ocr_engine_setting(tmp_path):
    """Plain text files always use the existing parser regardless of ocr_engine."""
    f = tmp_path / "notes.txt"
    f.write_text("Plain text content")

    result = parse_document(str(f), output_format="text", ocr_engine="chandra")
    assert "Plain text content" in result.text


# ---------------------------------------------------------------------------
# Executor integration - new fields passed through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_parse_step_passes_ocr_engine(tmp_path):
    """_execute_parse_step passes ocr_engine and languages from ParseConfig."""
    from sandcastle.engine.dag import StepDefinition

    f = tmp_path / "doc.txt"
    f.write_text("OCR engine test content")

    step = StepDefinition(
        id="parse_step",
        type="parse",
        prompt=str(f),
        parse_config=ParseConfig(ocr_engine="pymupdf", languages=["en"]),
    )
    ctx = _make_context()

    result = await _execute_parse_step(step, ctx)

    assert result.status == "completed"
    assert "OCR engine test content" in result.output["text"]


@pytest.mark.asyncio
async def test_execute_parse_step_chandra_import_error(tmp_path):
    """_execute_parse_step with chandra engine returns failed when chandra not installed."""
    from sandcastle.engine.dag import StepDefinition

    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    step = StepDefinition(
        id="parse_step",
        type="parse",
        prompt=str(f),
        parse_config=ParseConfig(ocr_engine="chandra"),
    )
    ctx = _make_context()

    with patch.dict("sys.modules", {"chandra_ocr": None}):
        result = await _execute_parse_step(step, ctx)

    assert result.status == "failed"
    assert "chandra" in result.error.lower()
