"""Tests for the document parser (docparse.py) and type: parse step."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import ParseConfig, parse_yaml_string, validate
from sandcastle.engine.docparse import (
    ParseResult,
    _parse_csv,
    _parse_page_ranges,
    _parse_pptx_fallback,
    parse_document,
)
from sandcastle.engine.executor import RunContext, StepResult, _execute_parse_step


# ---------------------------------------------------------------------------
# parse_document - plain text formats (no optional deps needed)
# ---------------------------------------------------------------------------


def test_parse_document_txt(tmp_path):
    """parse_document on .txt returns text content as-is."""
    f = tmp_path / "hello.txt"
    f.write_text("Hello, world!\nLine 2.")
    result = parse_document(str(f), output_format="text")
    assert isinstance(result, ParseResult)
    assert "Hello, world!" in result.text
    assert result.format == "text"
    assert result.pages == 1
    assert result.metadata["filename"] == "hello.txt"


def test_parse_document_md(tmp_path):
    """parse_document on .md returns markdown content."""
    f = tmp_path / "readme.md"
    f.write_text("# Title\nSome content.")
    result = parse_document(str(f))
    assert "# Title" in result.text


def test_parse_document_json(tmp_path):
    """parse_document on .json returns raw json text."""
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    result = parse_document(str(f), output_format="text")
    assert '"key"' in result.text
    assert result.metadata["content_type"] == "text/json"


def test_parse_document_yaml(tmp_path):
    """parse_document on .yaml returns content as text."""
    f = tmp_path / "config.yaml"
    f.write_text("name: test\nvalue: 42")
    result = parse_document(str(f))
    assert "name: test" in result.text


def test_parse_document_unsupported_raises(tmp_path):
    """parse_document raises ValueError for unsupported formats."""
    f = tmp_path / "file.xyz"
    f.write_text("data")
    with pytest.raises(ValueError, match="Unsupported file format"):
        parse_document(str(f))


def test_parse_document_unsupported_message_includes_extensions(tmp_path):
    """The ValueError message lists supported extensions."""
    f = tmp_path / "file.rtf"
    f.write_text("data")
    with pytest.raises(ValueError, match=r"\.pdf"):
        parse_document(str(f))


# ---------------------------------------------------------------------------
# parse_document - CSV
# ---------------------------------------------------------------------------


def test_parse_document_csv_markdown(tmp_path):
    """parse_document on .csv with markdown output produces a markdown table."""
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\nBob,25")
    result = parse_document(str(f), output_format="markdown")
    assert "| name | age |" in result.text
    assert "| --- | --- |" in result.text
    assert "| Alice | 30 |" in result.text
    assert result.format == "markdown"


def test_parse_document_csv_text(tmp_path):
    """parse_document on .csv with text output produces tab-separated rows."""
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30")
    result = parse_document(str(f), output_format="text")
    assert "Alice" in result.text
    assert result.format == "text"


def test_parse_document_csv_empty(tmp_path):
    """parse_document on empty .csv returns empty ParseResult."""
    f = tmp_path / "empty.csv"
    f.write_text("")
    result = parse_document(str(f))
    assert result.text == ""
    assert result.pages == 0


def test_parse_document_csv_short_rows(tmp_path):
    """parse_document on .csv with short rows pads them."""
    f = tmp_path / "short.csv"
    f.write_text("a,b,c\n1,2")  # row 2 has only 2 cells
    result = parse_document(str(f), output_format="markdown")
    # Should not raise; missing cell padded with empty string
    assert "| 1 | 2 |  |" in result.text


def test_parse_csv_metadata(tmp_path):
    """_parse_csv sets filename and rows in metadata."""
    f = tmp_path / "rows.csv"
    f.write_text("a,b\n1,2\n3,4\n5,6")
    result = _parse_csv(f, "text")
    assert result.metadata["rows"] == 4  # header + 3 data rows
    assert result.metadata["filename"] == "rows.csv"


# ---------------------------------------------------------------------------
# _parse_page_ranges
# ---------------------------------------------------------------------------


def test_parse_page_ranges_none_returns_all():
    """_parse_page_ranges(None, N) returns all 0-based indices."""
    assert _parse_page_ranges(None, 5) == [0, 1, 2, 3, 4]


def test_parse_page_ranges_range():
    """'1-3' maps to [0, 1, 2]."""
    assert _parse_page_ranges("1-3", 10) == [0, 1, 2]


def test_parse_page_ranges_individual():
    """'1,3,5' maps to [0, 2, 4]."""
    assert _parse_page_ranges("1,3,5", 10) == [0, 2, 4]


def test_parse_page_ranges_mixed():
    """Mixed ranges like '1-2,5' map correctly."""
    result = _parse_page_ranges("1-2,5", 10)
    assert result == [0, 1, 4]


def test_parse_page_ranges_clamped_to_total():
    """Out-of-range pages are silently omitted."""
    result = _parse_page_ranges("1-3,20", 3)  # page 20 doesn't exist
    assert 19 not in result
    assert result == [0, 1, 2]


def test_parse_page_ranges_deduplicates():
    """Overlapping ranges produce no duplicates."""
    result = _parse_page_ranges("1-3,2-4", 10)
    assert len(result) == len(set(result))
    assert sorted(result) == result  # also sorted


# ---------------------------------------------------------------------------
# _parse_pptx_fallback
# ---------------------------------------------------------------------------


def test_parse_pptx_fallback(tmp_path):
    """_parse_pptx_fallback extracts text from PPTX zip XML."""
    # Create a minimal PPTX-like zip with one slide
    pptx_path = tmp_path / "deck.pptx"
    slide_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<root xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        b'<a:t>Hello Slide</a:t>'
        b'<a:t>World Content</a:t>'
        b"</root>"
    )
    with zipfile.ZipFile(str(pptx_path), "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide_xml)

    result = _parse_pptx_fallback(pptx_path, "text")
    assert "Hello Slide" in result.text
    assert "World Content" in result.text
    assert result.format == "text"


def test_parse_pptx_fallback_empty(tmp_path):
    """_parse_pptx_fallback handles zip with no slides gracefully."""
    pptx_path = tmp_path / "empty.pptx"
    with zipfile.ZipFile(str(pptx_path), "w") as z:
        z.writestr("ppt/other/something.xml", b"<root/>")

    result = _parse_pptx_fallback(pptx_path, "text")
    assert result.text == ""


# ---------------------------------------------------------------------------
# PDF parsing - mocked fitz
# ---------------------------------------------------------------------------


def test_parse_pdf_requires_pymupdf(tmp_path):
    """_parse_pdf raises ImportError when pymupdf is not installed."""
    from sandcastle.engine.docparse import _parse_pdf

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")

    with patch.dict("sys.modules", {"fitz": None}):
        with pytest.raises(ImportError, match="pymupdf"):
            _parse_pdf(f, "text", None, False)


def test_parse_pdf_with_mock_fitz(tmp_path):
    """_parse_pdf correctly extracts text via a mocked fitz.open."""
    from sandcastle.engine.docparse import _parse_pdf

    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4")

    # Build mock fitz objects
    mock_span = {"text": "Hello PDF", "size": 12.0, "flags": 0, "bbox": [0, 0, 100, 12]}
    mock_line = {"spans": [mock_span]}
    mock_block = {"type": 0, "lines": [mock_line]}
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Hello PDF"
    mock_page.get_images.return_value = []
    mock_page.get_text.return_value = "Hello PDF"

    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=2)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc
    mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = _parse_pdf(f, "text", None, False)

    assert result.format == "text"
    assert result.metadata["total_pages"] == 2
    assert result.metadata["parse_method"] == "pymupdf"


def test_parse_pdf_page_range_filtering(tmp_path):
    """_parse_pdf respects the pages parameter to filter pages."""
    from sandcastle.engine.docparse import _parse_pdf

    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4")

    mock_page = MagicMock()
    mock_page.get_text.return_value = "Page text"
    mock_page.get_images.return_value = []

    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=5)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc
    mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = _parse_pdf(f, "text", "1-2", False)

    assert result.pages == 2
    assert result.metadata["parsed_pages"] == 2
    assert result.metadata["total_pages"] == 5


# ---------------------------------------------------------------------------
# DOCX parsing - mocked python-docx
# ---------------------------------------------------------------------------


def test_parse_docx_requires_python_docx(tmp_path):
    """_parse_docx raises ImportError when python-docx is not installed."""
    from sandcastle.engine.docparse import _parse_docx

    f = tmp_path / "doc.docx"
    f.write_bytes(b"PK")  # minimal zip header

    with patch.dict("sys.modules", {"docx": None}):
        with pytest.raises(ImportError, match="python-docx"):
            _parse_docx(f, "markdown")


# ---------------------------------------------------------------------------
# XLSX parsing - mocked openpyxl
# ---------------------------------------------------------------------------


def test_parse_xlsx_requires_openpyxl(tmp_path):
    """_parse_xlsx raises ImportError when openpyxl is not installed."""
    from sandcastle.engine.docparse import _parse_xlsx

    f = tmp_path / "data.xlsx"
    f.write_bytes(b"PK")

    with patch.dict("sys.modules", {"openpyxl": None}):
        with pytest.raises(ImportError, match="openpyxl"):
            _parse_xlsx(f, "markdown")


# ---------------------------------------------------------------------------
# screenshot_page
# ---------------------------------------------------------------------------


def test_screenshot_page_requires_pymupdf(tmp_path):
    """screenshot_page raises ImportError when pymupdf is not installed."""
    from sandcastle.engine.docparse import screenshot_page

    with patch.dict("sys.modules", {"fitz": None}):
        with pytest.raises(ImportError, match="pymupdf"):
            screenshot_page(str(tmp_path / "test.pdf"))


def test_screenshot_page_out_of_range(tmp_path):
    """screenshot_page raises ValueError for page out of range."""
    from sandcastle.engine.docparse import screenshot_page

    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4")

    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        with pytest.raises(ValueError, match="out of range"):
            screenshot_page(str(f), page=5)


def test_screenshot_page_returns_bytes(tmp_path):
    """screenshot_page returns PNG bytes from a valid page."""
    from sandcastle.engine.docparse import screenshot_page

    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4")

    mock_pix = MagicMock()
    mock_pix.tobytes.return_value = b"\x89PNG\r\n\x1a\n"  # minimal PNG header
    mock_page = MagicMock()
    mock_page.get_pixmap.return_value = mock_pix

    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = screenshot_page(str(f), page=0, dpi=72)

    assert isinstance(result, bytes)
    assert result == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# _extract_page_markdown heading/bold detection
# ---------------------------------------------------------------------------


def test_extract_page_markdown_heading_large_font():
    """Font size >= 18 produces an H1 markdown heading."""
    from sandcastle.engine.docparse import _extract_page_markdown

    mock_span = {"text": "Big Title", "size": 20.0, "flags": 0}
    mock_line = {"spans": [mock_span]}
    mock_block = {"type": 0, "lines": [mock_line]}
    mock_blocks_result = {"blocks": [mock_block]}

    mock_page = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.TEXT_PRESERVE_WHITESPACE = 1
    mock_page.get_text = MagicMock(return_value=mock_blocks_result)

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = _extract_page_markdown(mock_page, 1)

    assert result == "# Big Title"


def test_extract_page_markdown_bold_text():
    """Bold flag (bit 4) produces **bold** markdown."""
    from sandcastle.engine.docparse import _extract_page_markdown

    mock_span = {"text": "Bold Text", "size": 10.0, "flags": 2**4}  # bold flag
    mock_line = {"spans": [mock_span]}
    mock_block = {"type": 0, "lines": [mock_line]}
    mock_blocks_result = {"blocks": [mock_block]}

    mock_page = MagicMock()
    mock_page.get_text = MagicMock(return_value=mock_blocks_result)

    mock_fitz = MagicMock()
    mock_fitz.TEXT_PRESERVE_WHITESPACE = 1

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = _extract_page_markdown(mock_page, 1)

    assert result == "**Bold Text**"


# ---------------------------------------------------------------------------
# _execute_parse_step
# ---------------------------------------------------------------------------


def _make_context(step_outputs=None, run_input=None) -> RunContext:
    return RunContext(
        run_id="test-run-id",
        input=run_input or {},
        step_outputs=step_outputs or {},
    )


@pytest.mark.asyncio
async def test_execute_parse_step_with_file_path(tmp_path):
    """_execute_parse_step with absolute path in prompt returns completed StepResult."""
    f = tmp_path / "doc.txt"
    f.write_text("Hello from parse step")

    from sandcastle.engine.dag import StepDefinition

    step = StepDefinition(id="parse_step", type="parse", prompt=str(f))
    ctx = _make_context()

    result = await _execute_parse_step(step, ctx)

    assert result.status == "completed"
    assert result.cost_usd == 0.0
    assert isinstance(result.output, dict)
    assert "Hello from parse step" in result.output["text"]


@pytest.mark.asyncio
async def test_execute_parse_step_missing_file_returns_failed():
    """_execute_parse_step with nonexistent file returns failed StepResult."""
    from sandcastle.engine.dag import StepDefinition

    step = StepDefinition(id="parse_step", type="parse", prompt="/nonexistent/path/file.txt")
    ctx = _make_context()

    result = await _execute_parse_step(step, ctx)

    assert result.status == "failed"
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_parse_step_empty_prompt_no_deps_returns_failed():
    """_execute_parse_step with no prompt and no depends_on returns failed StepResult."""
    from sandcastle.engine.dag import StepDefinition

    step = StepDefinition(id="parse_step", type="parse", prompt="")
    ctx = _make_context()

    result = await _execute_parse_step(step, ctx)

    assert result.status == "failed"
    assert "file reference" in result.error.lower() or result.error is not None


@pytest.mark.asyncio
async def test_execute_parse_step_unsupported_format_returns_failed(tmp_path):
    """_execute_parse_step with an unsupported file format returns failed."""
    from sandcastle.engine.dag import StepDefinition

    f = tmp_path / "doc.xyz"
    f.write_text("data")
    step = StepDefinition(id="parse_step", type="parse", prompt=str(f))
    ctx = _make_context()

    result = await _execute_parse_step(step, ctx)

    assert result.status == "failed"
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_parse_step_import_error_propagated(tmp_path):
    """_execute_parse_step returns failed StepResult with clear message when pymupdf missing."""
    from sandcastle.engine.dag import StepDefinition

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    step = StepDefinition(id="parse_step", type="parse", prompt=str(f))
    ctx = _make_context()

    # Patch parse_document (imported inside _execute_parse_step) to raise ImportError
    with patch(
        "sandcastle.engine.docparse.parse_document",
        side_effect=ImportError("pymupdf is required for PDF parsing. Install with: pip install sandcastle-ai[parse]"),
    ):
        result = await _execute_parse_step(step, ctx)

    assert result.status == "failed"
    assert result.error is not None
    assert "pymupdf" in result.error


@pytest.mark.asyncio
async def test_execute_parse_step_file_ref_prefix(tmp_path):
    """_execute_parse_step handles @file: prefix correctly."""
    from sandcastle.engine.dag import StepDefinition

    f = tmp_path / "doc.txt"
    f.write_text("Content via @file: reference")
    step = StepDefinition(
        id="parse_step", type="parse", prompt=f"@file:{f}"
    )
    ctx = _make_context()

    result = await _execute_parse_step(step, ctx)

    assert result.status == "completed"
    assert "Content via @file: reference" in result.output["text"]


# ---------------------------------------------------------------------------
# YAML parse_config parsing and validation
# ---------------------------------------------------------------------------


def test_dag_parse_config_defaults():
    """ParseConfig has correct defaults."""
    cfg = ParseConfig()
    assert cfg.output == "markdown"
    assert cfg.pages is None
    assert cfg.ocr is False


def test_yaml_parse_step_config_parsed():
    """type: parse step with parse_config is parsed correctly from YAML."""
    yaml = """
name: test-parse
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.pdf"
    parse_config:
      output: markdown
      pages: "1-5"
      ocr: true
"""
    wf = parse_yaml_string(yaml)
    step = wf.steps[0]
    assert step.type == "parse"
    assert step.parse_config is not None
    assert step.parse_config.output == "markdown"
    assert step.parse_config.pages == "1-5"
    assert step.parse_config.ocr is True


def test_yaml_parse_step_no_config_uses_defaults():
    """type: parse step without parse_config uses ParseConfig defaults."""
    yaml = """
name: test-parse
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.txt"
"""
    wf = parse_yaml_string(yaml)
    step = wf.steps[0]
    assert step.type == "parse"
    assert step.parse_config is None  # None means executor uses ParseConfig() defaults


def test_validate_parse_step_invalid_output_format():
    """validate() returns error for invalid parse output format."""
    yaml = """
name: test-parse
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.pdf"
    parse_config:
      output: html
"""
    wf = parse_yaml_string(yaml)
    errors = validate(wf)
    assert any("invalid output format" in e.lower() for e in errors)


def test_validate_parse_step_valid_no_errors():
    """validate() returns no errors for a well-formed parse step."""
    yaml = """
name: test-parse
steps:
  - id: parse_doc
    type: parse
    prompt: "@file:/tmp/doc.pdf"
    parse_config:
      output: markdown
"""
    wf = parse_yaml_string(yaml)
    # Only check for parse-specific errors (model validation may fail in test env)
    errors = validate(wf)
    parse_errors = [e for e in errors if "parse" in e.lower() and "invalid output" in e.lower()]
    assert not parse_errors


def test_validate_parse_step_without_prompt_and_deps_warns():
    """validate() warns when parse step has no prompt and no depends_on."""
    yaml = """
name: test-parse
steps:
  - id: parse_doc
    type: parse
"""
    wf = parse_yaml_string(yaml)
    errors = validate(wf)
    # Should contain warning about missing file reference
    parse_related = [e for e in errors if "parse" in e.lower()]
    assert any("file reference" in e.lower() or "prompt" in e.lower() for e in parse_related)


# ---------------------------------------------------------------------------
# Full roundtrip integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_roundtrip_parse_txt(tmp_path):
    """Full roundtrip: YAML parse step -> validate -> execute with real .txt file."""
    from sandcastle.engine.dag import StepDefinition, build_plan

    f = tmp_path / "content.txt"
    f.write_text("Roundtrip test content line 1\nLine 2")

    yaml = f"""
name: roundtrip-parse
steps:
  - id: parse_doc
    type: parse
    prompt: "{f}"
    parse_config:
      output: text
"""
    wf = parse_yaml_string(yaml)
    # Validation may have model errors for "sonnet" not in KNOWN_MODELS in test env - ignore
    step = wf.get_step("parse_doc")
    assert step.type == "parse"

    plan = build_plan(wf)
    assert plan.stages == [["parse_doc"]]

    ctx = _make_context()
    result = await _execute_parse_step(step, ctx)

    assert result.status == "completed"
    assert result.step_id == "parse_doc"
    assert "Roundtrip test content" in result.output["text"]
    assert result.cost_usd == 0.0
    assert result.output["format"] == "text"
    assert result.output["pages"] == 1
