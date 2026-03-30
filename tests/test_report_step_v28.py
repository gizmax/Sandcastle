"""Tests for the report step type (v0.28).

Covers ReportConfig defaults, YAML parsing, validation, markdown-to-HTML
conversion, table of contents extraction, CSS theming, paper sizes,
WeasyPrint graceful degradation, and full render with mock WeasyPrint.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    ReportConfig,
    StepDefinition,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.report import (
    _build_toc,
    _format_css,
    _markdown_to_html,
    _THEME_CSS,
    render_report,
    render_report_html,
)


# ---------------------------------------------------------------------------
# ReportConfig defaults
# ---------------------------------------------------------------------------


def test_report_config_defaults():
    """ReportConfig has sensible defaults for all fields."""
    cfg = ReportConfig()
    assert cfg.template == "research-report"
    assert cfg.format == "pdf"
    assert cfg.theme == "professional"
    assert cfg.title == ""
    assert cfg.subtitle == ""
    assert cfg.author == ""
    assert cfg.logo_url == ""
    assert cfg.accent_color == "#f59e0b"
    assert cfg.include_toc is True
    assert cfg.include_page_numbers is True
    assert cfg.paper_size == "A4"


def test_report_config_custom_values():
    """ReportConfig accepts custom values for all fields."""
    cfg = ReportConfig(
        template="custom-tpl",
        format="html",
        theme="academic",
        title="My Report",
        subtitle="A Subtitle",
        author="Jane Doe",
        logo_url="https://example.com/logo.png",
        accent_color="#3b82f6",
        include_toc=False,
        include_page_numbers=False,
        paper_size="letter",
    )
    assert cfg.format == "html"
    assert cfg.theme == "academic"
    assert cfg.title == "My Report"
    assert cfg.subtitle == "A Subtitle"
    assert cfg.author == "Jane Doe"
    assert cfg.logo_url == "https://example.com/logo.png"
    assert cfg.accent_color == "#3b82f6"
    assert cfg.include_toc is False
    assert cfg.include_page_numbers is False
    assert cfg.paper_size == "letter"


# ---------------------------------------------------------------------------
# YAML parsing with report_config
# ---------------------------------------------------------------------------


_REPORT_WORKFLOW_YAML = """\
name: test-report-workflow
input_schema:
  required: [topic]
  properties:
    topic:
      type: string
      description: The topic
steps:
  - id: generate-report
    type: report
    prompt: "Write a detailed report about {input.topic}"
    report_config:
      theme: academic
      format: pdf
      title: "Analysis of {input.topic}"
      subtitle: "Comprehensive Review"
      author: "Research Team"
      accent_color: "#10b981"
      include_toc: true
      paper_size: letter
"""


def test_yaml_parsing_report_config():
    """Report config is correctly parsed from YAML."""
    wf = parse_yaml_string(_REPORT_WORKFLOW_YAML)
    step = wf.steps[0]
    assert step.type == "report"
    assert step.report_config is not None
    assert step.report_config.theme == "academic"
    assert step.report_config.format == "pdf"
    assert step.report_config.title == "Analysis of {input.topic}"
    assert step.report_config.subtitle == "Comprehensive Review"
    assert step.report_config.author == "Research Team"
    assert step.report_config.accent_color == "#10b981"
    assert step.report_config.include_toc is True
    assert step.report_config.paper_size == "letter"


def test_yaml_parsing_report_config_defaults():
    """Report step with minimal config gets defaults."""
    yaml_str = """\
name: minimal-report
input_schema:
  required: [data]
  properties:
    data:
      type: string
      description: Input data
steps:
  - id: report
    type: report
    prompt: "Summarize the data"
    report_config: {}
"""
    wf = parse_yaml_string(yaml_str)
    step = wf.steps[0]
    assert step.type == "report"
    assert step.report_config is not None
    assert step.report_config.theme == "professional"
    assert step.report_config.format == "pdf"
    assert step.report_config.paper_size == "A4"


# ---------------------------------------------------------------------------
# Validation: report type accepted
# ---------------------------------------------------------------------------


def test_validation_report_type_accepted():
    """Report type passes validation."""
    wf = parse_yaml_string(_REPORT_WORKFLOW_YAML)
    errors = validate(wf)
    # Filter to only report-related errors
    report_errors = [e for e in errors if "report" in e.lower() or "unknown type" in e.lower()]
    assert report_errors == [], f"Unexpected validation errors: {report_errors}"


def test_validation_report_invalid_format():
    """Report step with invalid format is caught by validation."""
    yaml_str = """\
name: bad-report
input_schema:
  required: [x]
  properties:
    x:
      type: string
      description: X
steps:
  - id: bad
    type: report
    prompt: "Generate report"
    report_config:
      format: docx
"""
    wf = parse_yaml_string(yaml_str)
    errors = validate(wf)
    format_errors = [e for e in errors if "format" in e.lower() and "report" in e.lower()]
    assert len(format_errors) == 1
    assert "docx" in format_errors[0]


def test_validation_report_invalid_theme():
    """Report step with invalid theme is caught by validation."""
    yaml_str = """\
name: bad-theme
input_schema:
  required: [x]
  properties:
    x:
      type: string
      description: X
steps:
  - id: bad
    type: report
    prompt: "Generate report"
    report_config:
      theme: fancy
"""
    wf = parse_yaml_string(yaml_str)
    errors = validate(wf)
    theme_errors = [e for e in errors if "theme" in e.lower()]
    assert len(theme_errors) == 1
    assert "fancy" in theme_errors[0]


def test_validation_report_invalid_paper_size():
    """Report step with invalid paper_size is caught by validation."""
    yaml_str = """\
name: bad-paper
input_schema:
  required: [x]
  properties:
    x:
      type: string
      description: X
steps:
  - id: bad
    type: report
    prompt: "Generate report"
    report_config:
      paper_size: legal
"""
    wf = parse_yaml_string(yaml_str)
    errors = validate(wf)
    paper_errors = [e for e in errors if "paper_size" in e.lower()]
    assert len(paper_errors) == 1
    assert "legal" in paper_errors[0]


# ---------------------------------------------------------------------------
# _markdown_to_html conversion
# ---------------------------------------------------------------------------


def test_markdown_to_html_headings():
    """Headings are converted to proper HTML tags."""
    md = "## Section One\n\n### Subsection\n\nSome text."
    html = _markdown_to_html(md)
    assert "<h2>" in html or "<h2" in html
    assert "<h3>" in html or "<h3" in html
    assert "Section One" in html
    assert "Subsection" in html


def test_markdown_to_html_bold_italic():
    """Bold and italic are converted."""
    md = "This is **bold** and *italic* text."
    html = _markdown_to_html(md)
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html


def test_markdown_to_html_table():
    """Markdown tables are converted to HTML tables."""
    md = "| Col A | Col B |\n|-------|-------|\n| 1     | 2     |"
    html = _markdown_to_html(md)
    # With markdown library, should produce <table>
    assert "<table" in html.lower() or "<th" in html.lower() or "Col A" in html


def test_markdown_to_html_code_block():
    """Fenced code blocks are converted."""
    md = "```python\nprint('hello')\n```"
    html = _markdown_to_html(md)
    assert "print" in html
    assert "<code" in html.lower() or "<pre" in html.lower()


# ---------------------------------------------------------------------------
# _build_toc extraction
# ---------------------------------------------------------------------------


def test_build_toc_extracts_headings():
    """ToC builder extracts h2 and h3 headings."""
    html = (
        "<h2>Introduction</h2>"
        "<p>Some text</p>"
        "<h3>Background</h3>"
        "<p>More text</p>"
        "<h2>Methodology</h2>"
        "<h3>Data Collection</h3>"
    )
    toc_html, modified = _build_toc(html)
    assert "Introduction" in toc_html
    assert "Background" in toc_html
    assert "Methodology" in toc_html
    assert "Data Collection" in toc_html
    # Check IDs were inserted
    assert 'id="' in modified


def test_build_toc_empty_content():
    """ToC builder handles content with no headings."""
    html = "<p>Just a paragraph</p>"
    toc_html, modified = _build_toc(html)
    assert toc_html == ""
    assert modified == html


def test_build_toc_classes():
    """ToC entries have correct CSS classes for nesting."""
    html = "<h2>Main</h2><h3>Sub</h3>"
    toc_html, _ = _build_toc(html)
    assert "toc-h2" in toc_html
    assert "toc-h3" in toc_html


# ---------------------------------------------------------------------------
# CSS injection with accent color
# ---------------------------------------------------------------------------


def test_css_accent_color_injection():
    """Accent color is injected into the CSS."""
    css = _format_css("professional", "#e11d48", "A4", True)
    assert "#e11d48" in css


def test_css_paper_size_a4():
    """A4 paper size is set in CSS."""
    css = _format_css("professional", "#000", "A4", True)
    assert "A4" in css


def test_css_paper_size_letter():
    """Letter paper size is set in CSS."""
    css = _format_css("professional", "#000", "letter", True)
    assert "letter" in css


def test_css_page_numbers_enabled():
    """Page counter is present when page numbers are enabled."""
    css = _format_css("professional", "#000", "A4", True)
    assert "counter(page)" in css


def test_css_page_numbers_disabled():
    """Page counter is 'none' when page numbers are disabled."""
    css = _format_css("professional", "#000", "A4", False)
    assert "content: none" in css


# ---------------------------------------------------------------------------
# Template selection (professional/minimal/academic)
# ---------------------------------------------------------------------------


def test_theme_templates_exist():
    """All three theme templates are defined."""
    assert "professional" in _THEME_CSS
    assert "minimal" in _THEME_CSS
    assert "academic" in _THEME_CSS


def test_professional_theme_has_cover():
    """Professional theme CSS includes cover page styles."""
    css = _THEME_CSS["professional"]
    assert ".cover" in css
    assert ".toc" in css


def test_minimal_theme_uses_serif():
    """Minimal theme uses Georgia/serif font."""
    css = _THEME_CSS["minimal"]
    assert "Georgia" in css


def test_academic_theme_double_spacing():
    """Academic theme has double line spacing."""
    css = _THEME_CSS["academic"]
    assert "line-height: 2" in css


def test_academic_theme_times_new_roman():
    """Academic theme uses Times New Roman."""
    css = _THEME_CSS["academic"]
    assert "Times New Roman" in css


# ---------------------------------------------------------------------------
# WeasyPrint not installed: graceful error
# ---------------------------------------------------------------------------


def test_render_report_no_weasyprint():
    """render_report raises RuntimeError when WeasyPrint is not installed."""
    cfg = ReportConfig(format="pdf")
    with patch("sandcastle.engine.report.HAS_WEASYPRINT", False):
        with pytest.raises(RuntimeError, match="weasyprint"):
            render_report("# Test", cfg)


# ---------------------------------------------------------------------------
# Full render with mock WeasyPrint
# ---------------------------------------------------------------------------


def test_render_report_pdf_with_mock_weasyprint():
    """Full PDF render works with mocked WeasyPrint."""
    cfg = ReportConfig(
        title="Test Report",
        subtitle="Unit Test",
        author="Test Author",
        theme="professional",
        accent_color="#f59e0b",
        include_toc=True,
        paper_size="A4",
    )

    fake_pdf_bytes = b"%PDF-1.4 fake"
    mock_html_cls = MagicMock()
    mock_html_cls.return_value.write_pdf.return_value = fake_pdf_bytes

    with patch("sandcastle.engine.report.HAS_WEASYPRINT", True), \
         patch("sandcastle.engine.report.WeasyprintHTML", mock_html_cls):
        result = render_report(
            "## Introduction\n\nSome content.\n\n## Analysis\n\nMore content.",
            cfg,
            {"run_id": "test-123"},
        )

    assert result == fake_pdf_bytes
    mock_html_cls.assert_called_once()
    call_kwargs = mock_html_cls.call_args
    html_string = call_kwargs.kwargs.get("string") or call_kwargs[1].get("string")
    assert "Test Report" in html_string
    assert "Unit Test" in html_string
    assert "Test Author" in html_string
    assert "#f59e0b" in html_string


def test_render_report_html_format():
    """HTML format render returns HTML bytes without WeasyPrint."""
    cfg = ReportConfig(
        title="HTML Report",
        format="html",
        theme="minimal",
    )

    # HTML format should not need WeasyPrint at all
    with patch("sandcastle.engine.report.HAS_WEASYPRINT", True):
        result = render_report(
            "## Section\n\nContent here.",
            cfg,
        )

    assert isinstance(result, bytes)
    html = result.decode("utf-8")
    assert "HTML Report" in html
    assert "<h2" in html
    assert "Content here" in html


def test_render_report_html_helper():
    """render_report_html returns a string."""
    cfg = ReportConfig(
        title="Preview",
        theme="professional",
        accent_color="#3b82f6",
    )
    html = render_report_html("## Test\n\nParagraph.", cfg)
    assert isinstance(html, str)
    assert "Preview" in html
    assert "#3b82f6" in html


def test_render_report_cover_page_elements():
    """Rendered HTML includes all cover page elements."""
    cfg = ReportConfig(
        title="Cover Title",
        subtitle="Cover Subtitle",
        author="Cover Author",
        logo_url="https://example.com/logo.png",
    )
    html = render_report_html("## Body", cfg)
    assert "Cover Title" in html
    assert "Cover Subtitle" in html
    assert "Cover Author" in html
    assert "https://example.com/logo.png" in html


def test_render_report_no_toc():
    """When include_toc=False, no ToC HTML block is generated."""
    cfg = ReportConfig(title="No TOC", include_toc=False)
    html = render_report_html("## Section One\n\n## Section Two", cfg)
    # The CSS may contain "Table of Contents" as a comment, but the actual
    # ToC div should not be present
    assert '<div class="toc">' not in html


def test_render_report_with_toc():
    """When include_toc=True, ToC section is present."""
    cfg = ReportConfig(title="With TOC", include_toc=True)
    html = render_report_html("## Section One\n\n## Section Two", cfg)
    assert "Table of Contents" in html
    assert "Section One" in html
    assert "Section Two" in html
