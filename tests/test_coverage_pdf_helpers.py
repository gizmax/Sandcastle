"""Coverage for engine/pdf.py helper functions.

Targets: _strip_inline_md, _extract_numeric, _extract_severity_score,
         _parse_kpi_markers, _detect_callout, _find_unicode_font,
         generate_branded_pdf
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# _strip_inline_md
# ===========================================================================

class TestStripInlineMd:
    """Cover _strip_inline_md function."""

    def test_strip_bold(self):
        """_strip_inline_md removes bold markdown."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("**bold text** and normal")
        assert "bold text" in result
        assert "**" not in result

    def test_strip_italic(self):
        """_strip_inline_md removes italic markdown."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("*italic* text")
        assert "italic" in result
        assert "*" not in result

    def test_strip_code_inline(self):
        """_strip_inline_md removes inline code."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("Run `python --version` now")
        assert "python --version" in result
        assert "`" not in result

    def test_strip_link(self):
        """_strip_inline_md extracts link text."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("[click here](https://example.com)")
        assert "click here" in result
        assert "https" not in result

    def test_strip_strikethrough(self):
        """_strip_inline_md removes strikethrough."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("~~old value~~ new value")
        assert "old value" in result
        assert "~~" not in result

    def test_strip_html_tags(self):
        """_strip_inline_md removes HTML tags."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("Text <br/> with <strong>html</strong>")
        assert "html" in result
        assert "<" not in result

    def test_strip_long_text_truncated(self):
        """_strip_inline_md truncates text over 10000 chars."""
        from sandcastle.engine.pdf import _strip_inline_md
        long_text = "A" * 11000
        result = _strip_inline_md(long_text)
        assert len(result) <= 10001  # Some slack for processing

    def test_strip_empty_string(self):
        """_strip_inline_md handles empty string."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("")
        assert result == ""

    def test_strip_multiple_spaces(self):
        """_strip_inline_md collapses multiple spaces."""
        from sandcastle.engine.pdf import _strip_inline_md
        result = _strip_inline_md("word    word")
        assert "  " not in result


# ===========================================================================
# _extract_numeric
# ===========================================================================

class TestExtractNumeric:
    """Cover _extract_numeric function."""

    def test_extract_plain_number(self):
        """_extract_numeric extracts plain integer."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("42") == 42.0

    def test_extract_float(self):
        """_extract_numeric extracts float."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("3.14") == 3.14

    def test_extract_with_dollar(self):
        """_extract_numeric handles dollar sign."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("$1234.56") == 1234.56

    def test_extract_with_comma_separator(self):
        """_extract_numeric handles thousand separators."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("1,234") == 1234.0

    def test_extract_with_percent(self):
        """_extract_numeric handles percentage."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("42%") == 42.0

    def test_extract_empty_string(self):
        """_extract_numeric returns None for empty string."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("") is None

    def test_extract_non_numeric(self):
        """_extract_numeric returns None for non-numeric text."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("N/A") is None

    def test_extract_nan(self):
        """_extract_numeric returns None for NaN."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("nan") is None

    def test_extract_infinity(self):
        """_extract_numeric returns None for infinity."""
        from sandcastle.engine.pdf import _extract_numeric
        assert _extract_numeric("inf") is None


# ===========================================================================
# _extract_severity_score
# ===========================================================================

class TestExtractSeverityScore:
    """Cover _extract_severity_score function."""

    def test_extract_critical(self):
        """_extract_severity_score returns 4 for critical."""
        from sandcastle.engine.pdf import _extract_severity_score
        assert _extract_severity_score("critical") == 4

    def test_extract_high(self):
        """_extract_severity_score returns 3 for high."""
        from sandcastle.engine.pdf import _extract_severity_score
        assert _extract_severity_score("high") == 3

    def test_extract_medium(self):
        """_extract_severity_score returns 2 for medium."""
        from sandcastle.engine.pdf import _extract_severity_score
        assert _extract_severity_score("medium") == 2

    def test_extract_low(self):
        """_extract_severity_score returns 1 for low."""
        from sandcastle.engine.pdf import _extract_severity_score
        assert _extract_severity_score("low") == 1

    def test_extract_p0(self):
        """_extract_severity_score handles P0/P1 labels."""
        from sandcastle.engine.pdf import _extract_severity_score
        assert _extract_severity_score("p0") == 4

    def test_extract_unknown(self):
        """_extract_severity_score returns None for unknown text."""
        from sandcastle.engine.pdf import _extract_severity_score
        assert _extract_severity_score("unknown_severity") is None

    def test_extract_with_markdown(self):
        """_extract_severity_score handles markdown-wrapped text."""
        from sandcastle.engine.pdf import _extract_severity_score
        result = _extract_severity_score("**High**")
        assert result == 3


# ===========================================================================
# _parse_kpi_markers
# ===========================================================================

class TestParseKpiMarkers:
    """Cover _parse_kpi_markers function."""

    def test_parse_valid_kpi_markers(self):
        """_parse_kpi_markers parses KPI comment markers."""
        from sandcastle.engine.pdf import _parse_kpi_markers
        line = "<!-- kpi: Revenue=$2.4M(+12%)|NPS=72(+5pts) -->"
        result = _parse_kpi_markers(line)
        assert result is not None
        assert len(result) == 2
        # First metric: Revenue
        label, value, change, positive = result[0]
        assert label == "Revenue"
        assert value == "$2.4M"
        assert change == "+12%"
        assert positive is True

    def test_parse_kpi_with_negative_change(self):
        """_parse_kpi_markers handles negative change."""
        from sandcastle.engine.pdf import _parse_kpi_markers
        line = "<!-- kpi: Churn=5%(-2pts) -->"
        result = _parse_kpi_markers(line)
        assert result is not None
        label, value, change, positive = result[0]
        assert positive is False

    def test_parse_kpi_no_change(self):
        """_parse_kpi_markers handles KPI without change."""
        from sandcastle.engine.pdf import _parse_kpi_markers
        line = "<!-- kpi: Score=9.2 -->"
        result = _parse_kpi_markers(line)
        assert result is not None
        label, value, change, positive = result[0]
        assert value == "9.2"
        assert change == ""

    def test_parse_kpi_not_matching(self):
        """_parse_kpi_markers returns None for non-KPI lines."""
        from sandcastle.engine.pdf import _parse_kpi_markers
        result = _parse_kpi_markers("Just a normal line")
        assert result is None

    def test_parse_kpi_empty_string(self):
        """_parse_kpi_markers returns None for empty string."""
        from sandcastle.engine.pdf import _parse_kpi_markers
        result = _parse_kpi_markers("")
        assert result is None


# ===========================================================================
# _detect_callout
# ===========================================================================

class TestDetectCallout:
    """Cover _detect_callout function."""

    def test_detect_warning_callout(self):
        """_detect_callout detects GitHub-style WARNING callout."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("> [!WARNING] This is a warning")
        assert result is not None
        level, text = result
        assert level == "warning"
        assert "warning" in text.lower() or "This" in text

    def test_detect_note_callout(self):
        """_detect_callout detects NOTE callout."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("> [!NOTE] Important note here")
        assert result is not None
        level, text = result
        assert level == "note"

    def test_detect_important_callout(self):
        """_detect_callout detects IMPORTANT callout."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("> [!IMPORTANT] Critical info")
        assert result is not None
        level, text = result
        assert level == "important"

    def test_detect_tip_callout(self):
        """_detect_callout detects TIP callout."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("> [!TIP] Helpful tip")
        assert result is not None
        level, text = result
        assert level == "note"  # TIP maps to "note"

    def test_detect_bold_warning_callout(self):
        """_detect_callout detects **Warning:** callout."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("> **Warning:** Be careful here")
        assert result is not None

    def test_detect_no_callout(self):
        """_detect_callout returns None for regular text."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("Regular paragraph text")
        assert result is None

    def test_detect_blockquote_no_marker(self):
        """_detect_callout returns None for regular blockquote."""
        from sandcastle.engine.pdf import _detect_callout
        result = _detect_callout("> Just a regular quote")
        assert result is None


# ===========================================================================
# generate_branded_pdf
# ===========================================================================

class TestGenerateBrandedPdf:
    """Cover generate_branded_pdf function."""

    def test_generate_pdf_with_simple_markdown(self, tmp_path):
        """generate_branded_pdf creates a PDF from markdown."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = """# Test Report

## Summary

This is a test report with basic content.

- Item one
- Item two
- Item three

## Details

Some additional information here.
"""
        output_path = tmp_path / "test.pdf"
        # Should not raise
        generate_branded_pdf(md_content, output_path)
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_generate_pdf_with_kpi_markers(self, tmp_path):
        """generate_branded_pdf handles KPI markers."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = """# KPI Report

<!-- kpi: Revenue=$2.4M(+12%)|NPS=72(+5pts) -->

## Analysis

Revenue has increased significantly.
"""
        output_path = tmp_path / "kpi.pdf"
        generate_branded_pdf(md_content, output_path)
        assert output_path.exists()

    def test_generate_pdf_with_table(self, tmp_path):
        """generate_branded_pdf handles markdown tables."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = """# Table Report

## Data Table

| Name | Score | Grade |
|------|-------|-------|
| Alice | 95 | A |
| Bob | 82 | B |
| Carol | 78 | C |

Analysis complete.
"""
        output_path = tmp_path / "table.pdf"
        generate_branded_pdf(md_content, output_path)
        assert output_path.exists()

    def test_generate_pdf_with_callout(self, tmp_path):
        """generate_branded_pdf handles callout blocks."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = """# Alert Report

> [!WARNING] This is important

> [!NOTE] Additional note here

Regular content.
"""
        output_path = tmp_path / "callout.pdf"
        generate_branded_pdf(md_content, output_path)
        assert output_path.exists()

    def test_generate_pdf_path_as_string(self, tmp_path):
        """generate_branded_pdf accepts string path."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = "# Simple\n\nJust a simple report.\n"
        output_path = str(tmp_path / "string_path.pdf")
        generate_branded_pdf(md_content, output_path)
        assert Path(output_path).exists()

    def test_generate_pdf_with_headers(self, tmp_path):
        """generate_branded_pdf handles various header levels."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = """# H1 Title

## H2 Section

### H3 Subsection

#### H4 Detail

Regular paragraph text with **bold** and *italic*.
"""
        output_path = tmp_path / "headers.pdf"
        generate_branded_pdf(md_content, output_path)
        assert output_path.exists()

    def test_generate_pdf_with_code_block(self, tmp_path):
        """generate_branded_pdf handles code blocks."""
        from sandcastle.engine.pdf import generate_branded_pdf

        md_content = """# Code Example

```python
def hello():
    print("Hello, World!")
```

The code above prints a greeting.
"""
        output_path = tmp_path / "code.pdf"
        generate_branded_pdf(md_content, output_path)
        assert output_path.exists()
