"""Modern PDF report renderer with charts, KPI tiles, and visual hierarchy.

Uses fpdf2 for PDF generation and matplotlib for chart images.
Produces professional, branded reports with auto-detected data visualizations.
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger(__name__)

# Optional matplotlib for chart generation
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logger.info("matplotlib not available, chart generation disabled")

_FONT = "sandcastle"
_MONO = "sandcastle-mono"

# -- Brand colors -----------------------------------------------------------
_ACCENT = (59, 130, 246)       # blue-500
_ACCENT_HEX = "#3B82F6"
_ACCENT_LIGHT = (219, 234, 254)  # blue-100
_ACCENT_DARK = (29, 78, 216)  # blue-700
_H1_COLOR = (17, 24, 39)      # gray-900
_H2_COLOR = (31, 41, 55)      # gray-800
_H3_COLOR = (55, 65, 81)      # gray-700
_H4_COLOR = (75, 85, 99)      # gray-600
_BODY_COLOR = (55, 65, 81)    # gray-700
_MUTED = (107, 114, 128)      # gray-500
_TABLE_HEADER_BG = (243, 244, 246)   # gray-100
_TABLE_HEADER_FG = (31, 41, 55)      # gray-800
_TABLE_BORDER = (229, 231, 235)      # gray-200
_TABLE_ALT_BG = (249, 250, 251)      # gray-50
_WHITE = (255, 255, 255)

# Chart color palette
_CHART_COLORS = [
    "#3B82F6", "#8B5CF6", "#06B6D4", "#F59E0B",
    "#10B981", "#EF4444", "#EC4899", "#6366F1",
]

# Severity badge colors: keyword -> (bg, text)
_SEVERITY_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "critical": ((254, 226, 226), (153, 27, 27)),
    "high": ((254, 243, 199), (146, 64, 14)),
    "medium": ((219, 234, 254), (30, 64, 175)),
    "low": ((220, 252, 231), (22, 101, 52)),
}
# Czech severity variants (all gender forms)
for _k, _src in [
    ("kriticka", "critical"), ("kriticky", "critical"), ("kriticke", "critical"),
    ("vysoka", "high"), ("vysoky", "high"), ("vysoke", "high"),
    ("stredni", "medium"),
    ("nizka", "low"), ("nizky", "low"), ("nizke", "low"),
    ("p0", "critical"), ("p1", "high"), ("p2", "medium"), ("p3", "low"),
]:
    _SEVERITY_COLORS[_k] = _SEVERITY_COLORS[_src]

_LINE_H = 5


# -- Font discovery ---------------------------------------------------------

def _find_unicode_font() -> tuple[str | None, str | None, str | None, str | None]:
    """Find a Unicode TTF font. Returns (regular, bold, italic, mono) paths."""
    candidates = [
        {
            "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "mono": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        },
        {
            "regular": "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "bold": "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "italic": "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf",
            "mono": "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        },
        {
            "regular": "/Library/Fonts/Arial Unicode.ttf",
            "bold": "/Library/Fonts/Arial Unicode.ttf",
            "italic": "/Library/Fonts/Arial Unicode.ttf",
            "mono": "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
        },
        {
            "regular": "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "bold": "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "italic": "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "mono": "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
        },
        {
            "regular": "C:/Windows/Fonts/arial.ttf",
            "bold": "C:/Windows/Fonts/arialbd.ttf",
            "italic": "C:/Windows/Fonts/ariali.ttf",
            "mono": "C:/Windows/Fonts/consola.ttf",
        },
    ]
    for c in candidates:
        if Path(c["regular"]).exists():
            return (
                c["regular"],
                c["bold"] if Path(c["bold"]).exists() else c["regular"],
                c["italic"] if Path(c["italic"]).exists() else c["regular"],
                c.get("mono") if Path(c.get("mono", "")).exists() else c["regular"],
            )
    return None, None, None, None


# -- Chart generation -------------------------------------------------------

class _ChartGen:
    """Generate chart images using matplotlib for PDF embedding."""

    def __init__(self) -> None:
        self._tmpfiles: list[str] = []

    def cleanup(self) -> None:
        for f in self._tmpfiles:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass

    def _save(self, fig: object) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        fig.savefig(
            tmp.name, dpi=150, bbox_inches="tight",
            facecolor="white", edgecolor="none",
        )
        plt.close(fig)
        self._tmpfiles.append(tmp.name)
        return tmp.name

    def radar(
        self, categories: list[str], series: dict[str, list[float]],
    ) -> str | None:
        """Radar/spider chart for multi-axis comparison."""
        if not HAS_MATPLOTLIB or not categories or not series:
            return None
        n = len(categories)
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(4.5, 4.5), subplot_kw={"polar": True})
        ax.set_theta_offset(np.pi / 2)
        ax.set_theta_direction(-1)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, size=7, color="#374151")
        ax.tick_params(axis="y", labelsize=6, colors="#9CA3AF")
        ax.spines["polar"].set_color("#E5E7EB")
        ax.set_facecolor("#FAFAFA")

        for i, (name, vals) in enumerate(series.items()):
            v = vals + vals[:1]
            color = _CHART_COLORS[i % len(_CHART_COLORS)]
            ax.plot(angles, v, "o-", lw=1.8, label=name, color=color, ms=4)
            ax.fill(angles, v, alpha=0.12, color=color)

        ax.legend(
            loc="upper right", bbox_to_anchor=(1.35, 1.1),
            fontsize=7, framealpha=0.9,
        )
        fig.tight_layout()
        return self._save(fig)

    def donut(
        self, labels: list[str], values: list[float], title: str = "",
    ) -> str | None:
        """Donut chart for proportional data."""
        if not HAS_MATPLOTLIB or not labels:
            return None
        fig, ax = plt.subplots(figsize=(4, 3.5))
        colors = [_CHART_COLORS[i % len(_CHART_COLORS)] for i in range(len(labels))]

        wedges, texts, autotexts = ax.pie(
            values, labels=None, autopct="%1.0f%%",
            colors=colors, startangle=90, pctdistance=0.78,
            wedgeprops={"edgecolor": "white", "linewidth": 2},
        )
        centre = plt.Circle((0, 0), 0.55, fc="white")
        ax.add_patch(centre)

        for t in autotexts:
            t.set_fontsize(8)
            t.set_fontweight("bold")
            t.set_color("#374151")

        ax.legend(
            wedges, labels, loc="center left", bbox_to_anchor=(1, 0.5),
            fontsize=7, frameon=False,
        )
        if title:
            ax.set_title(title, fontsize=10, fontweight="bold", color="#1F2937")
        fig.tight_layout()
        return self._save(fig)

    def horizontal_bars(
        self, labels: list[str], values: list[float], title: str = "",
    ) -> str | None:
        """Horizontal bar chart with gradient-like coloring."""
        if not HAS_MATPLOTLIB or not labels:
            return None
        fig, ax = plt.subplots(figsize=(5.5, max(1.8, len(labels) * 0.45)))
        max_val = max(values) if values else 1

        # Color gradient: higher values get more saturated accent color
        colors = []
        for v in values:
            ratio = v / max_val if max_val else 0
            r = int(219 + (59 - 219) * ratio)
            g = int(234 + (130 - 234) * ratio)
            b = int(254 + (246 - 254) * ratio)
            colors.append(f"#{r:02x}{g:02x}{b:02x}")

        y_pos = list(range(len(labels)))
        bars = ax.barh(
            y_pos, values, color=colors, height=0.55,
            edgecolor="none",
        )
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8, color="#374151")
        ax.invert_yaxis()

        for bar, val in zip(bars, values):
            fmt = f"{val:,.0f}" if val == int(val) else f"{val:,.1f}"
            ax.text(
                bar.get_width() + max_val * 0.02,
                bar.get_y() + bar.get_height() / 2,
                fmt, va="center", fontsize=8, fontweight="bold", color="#3B82F6",
            )

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#E5E7EB")
        ax.spines["left"].set_color("#E5E7EB")
        ax.tick_params(axis="x", colors="#9CA3AF", labelsize=7)

        if title:
            ax.set_title(title, fontsize=10, fontweight="bold", loc="left", color="#1F2937")
        fig.tight_layout()
        return self._save(fig)

    def gauge(
        self, value: float, max_val: float = 10, label: str = "",
    ) -> str | None:
        """Semi-circular gauge for score display."""
        if not HAS_MATPLOTLIB:
            return None
        fig, ax = plt.subplots(figsize=(2.5, 1.6))

        theta_bg = np.linspace(np.pi, 0, 100)
        ax.plot(
            np.cos(theta_bg), np.sin(theta_bg),
            color="#E5E7EB", lw=12, solid_capstyle="round",
        )

        ratio = min(value / max_val, 1.0) if max_val else 0
        if ratio > 0:
            theta_val = np.linspace(np.pi, np.pi * (1 - ratio), 100)
            color = "#3B82F6" if ratio < 0.6 else "#10B981" if ratio < 0.85 else "#F59E0B"
            ax.plot(
                np.cos(theta_val), np.sin(theta_val),
                color=color, lw=12, solid_capstyle="round",
            )

        fmt = f"{value:g}" if value == int(value) else f"{value:.1f}"
        ax.text(
            0, 0.15, fmt, ha="center", va="center",
            fontsize=20, fontweight="bold", color="#1F2937",
        )
        ax.text(0, -0.15, f"/ {max_val:g}", ha="center", va="center", fontsize=9, color="#9CA3AF")
        if label:
            ax.text(0, -0.45, label, ha="center", va="center", fontsize=8, color="#6B7280")

        ax.set_xlim(-1.3, 1.3)
        ax.set_ylim(-0.6, 1.3)
        ax.set_aspect("equal")
        ax.axis("off")
        fig.tight_layout()
        return self._save(fig)

    def score_bars(
        self, items: list[tuple[str, float, float]], title: str = "",
    ) -> str | None:
        """Grouped horizontal score bars. items: (label, value, max_val)."""
        if not HAS_MATPLOTLIB or not items:
            return None
        fig, ax = plt.subplots(figsize=(5.5, max(1.5, len(items) * 0.4)))

        labels = [it[0] for it in items]
        values = [it[1] for it in items]
        maxvals = [it[2] for it in items]
        y_pos = list(range(len(labels)))

        # Background bars (max)
        ax.barh(y_pos, maxvals, color="#F3F4F6", height=0.45, edgecolor="none")
        # Value bars
        colors = []
        for v, m in zip(values, maxvals):
            r = v / m if m else 0
            colors.append("#10B981" if r >= 0.7 else "#3B82F6" if r >= 0.4 else "#EF4444")
        ax.barh(y_pos, values, color=colors, height=0.45, edgecolor="none")

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8, color="#374151")
        ax.invert_yaxis()
        ax.set_xlim(0, max(maxvals) * 1.15)

        for i, (v, m) in enumerate(zip(values, maxvals)):
            ax.text(
                v + max(maxvals) * 0.02, i, f"{v:g}/{m:g}",
                va="center", fontsize=7, color="#6B7280",
            )

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_visible(False)
        ax.spines["left"].set_color("#E5E7EB")
        ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)

        if title:
            ax.set_title(title, fontsize=10, fontweight="bold", loc="left", color="#1F2937")
        fig.tight_layout()
        return self._save(fig)


# -- PDF class --------------------------------------------------------------

class _BrandedPDF(FPDF):
    """FPDF subclass with Sandcastle branding and Unicode support."""

    _unicode_available = False

    def __init__(self, language: str = "en") -> None:
        super().__init__()
        self.language = language
        self.set_auto_page_break(auto=True, margin=22)
        self._setup_fonts()

    def _setup_fonts(self) -> None:
        regular, bold, italic, mono = _find_unicode_font()
        if regular:
            self.add_font(_FONT, style="", fname=regular)
            self.add_font(_FONT, style="B", fname=bold)
            self.add_font(_FONT, style="I", fname=italic)
            self.add_font(_MONO, style="", fname=mono)
            self._unicode_available = True
        else:
            logger.warning("No Unicode TTF font found, falling back to Helvetica")

    @property
    def fn(self) -> str:
        return _FONT if self._unicode_available else "Helvetica"

    @property
    def mn(self) -> str:
        return _MONO if self._unicode_available else "Courier"

    @property
    def bullet(self) -> str:
        return "\u2022" if self._unicode_available else "-"

    def header(self) -> None:
        # Accent bar at very top
        self.set_fill_color(*_ACCENT)
        self.rect(0, 0, self.w, 2.5, "F")
        # Header text
        self.set_y(5)
        self.set_font(self.fn, "", 7)
        self.set_text_color(*_MUTED)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hdr = f"SANDCASTLE REPORT  |  {date_str}"
        self.cell(0, 5, hdr, align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(self.fn, "", 7)
        self.set_text_color(*_MUTED)
        self.set_draw_color(*_TABLE_BORDER)
        self.set_line_width(0.2)
        self.line(10, self.get_y(), self.w - 10, self.get_y())
        txt = f"Sandcastle by Tomas Pflanzer  |  Page {self.page_no()}/{{nb}}"
        self.cell(0, 10, txt, align="C")


# -- Inline markdown stripping ---------------------------------------------

def _strip_inline_md(text: str) -> str:
    """Remove inline markdown formatting and HTML tags."""
    # Strip HTML tags: <br>, <br/>, <strong>, etc.
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Inline markdown
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Collapse multiple spaces
    text = re.sub(r"  +", " ", text)
    return text.strip()


# -- Severity badge ---------------------------------------------------------

def _draw_badge(
    pdf: _BrandedPDF, text: str, x: float, y: float, w: float, h: float,
) -> bool:
    """Draw a colored badge if text matches a severity keyword."""
    key = re.sub(r"[^a-z0-9]", "", text.lower().strip())
    colors = _SEVERITY_COLORS.get(key)
    if not colors:
        return False
    bg, fg = colors
    pdf.set_fill_color(*bg)
    pdf.set_text_color(*fg)
    pdf.set_font(pdf.fn, "B", 7)
    rx, ry = x + 1, y + 0.5
    rw = min(w - 2, pdf.get_string_width(text) + 6)
    rh = h - 1
    pdf.rect(rx, ry, rw, rh, "F")
    pdf.set_xy(rx, ry)
    pdf.cell(rw, rh, text, align="C")
    return True


# -- KPI tiles --------------------------------------------------------------

def _render_kpi_tiles(
    pdf: _BrandedPDF, metrics: list[tuple[str, str, str, bool]],
) -> None:
    """Render a row of KPI metric cards.

    metrics: list of (label, value, change_text, is_positive).
    """
    if not metrics:
        return

    n = min(len(metrics), 4)
    usable = pdf.w - 20
    tile_w = usable / n
    tile_h = 22
    gap = 3
    start_x = 10
    start_y = pdf.get_y()

    if start_y + tile_h + 5 > pdf.h - 25:
        pdf.add_page()
        start_y = pdf.get_y()

    for i, (label, value, change, positive) in enumerate(metrics[:4]):
        x = start_x + i * tile_w + (gap / 2 if i > 0 else 0)
        w = tile_w - gap

        # Card background with subtle border
        pdf.set_fill_color(249, 250, 251)
        pdf.set_draw_color(*_TABLE_BORDER)
        pdf.set_line_width(0.3)
        pdf.rect(x, start_y, w, tile_h, "DF")

        # Left accent bar
        if positive:
            pdf.set_fill_color(16, 185, 129)  # emerald-500
        else:
            pdf.set_fill_color(239, 68, 68)  # red-500
        pdf.rect(x, start_y, 2, tile_h, "F")

        # Label
        pdf.set_font(pdf.fn, "", 7)
        pdf.set_text_color(*_MUTED)
        pdf.set_xy(x + 5, start_y + 2)
        pdf.cell(w - 8, 4, label.upper())

        # Value
        pdf.set_font(pdf.fn, "B", 14)
        pdf.set_text_color(*_H1_COLOR)
        pdf.set_xy(x + 5, start_y + 7)
        pdf.cell(w - 8, 7, value)

        # Change indicator
        arrow = "\u2191" if positive else "\u2193"
        if not pdf._unicode_available:
            arrow = "+" if positive else "-"
        pdf.set_font(pdf.fn, "B", 8)
        if positive:
            pdf.set_text_color(16, 185, 129)
        else:
            pdf.set_text_color(239, 68, 68)
        pdf.set_xy(x + 5, start_y + 15)
        pdf.cell(w - 8, 4, f"{arrow} {change}")

    pdf.set_y(start_y + tile_h + 4)


# -- Callout boxes ----------------------------------------------------------

_CALLOUT_STYLES = {
    "note": ((_ACCENT_LIGHT), _ACCENT, "INFO"),
    "warning": ((254, 243, 199), (146, 64, 14), "WARNING"),
    "important": ((254, 226, 226), (153, 27, 27), "IMPORTANT"),
}


def _render_callout(pdf: _BrandedPDF, text: str, level: str = "note") -> None:
    """Render a callout/alert box."""
    bg, accent, title = _CALLOUT_STYLES.get(level, _CALLOUT_STYLES["note"])
    text = _strip_inline_md(text)

    pdf.ln(2)
    start_y = pdf.get_y()
    # Measure text height
    pdf.set_font(pdf.fn, "", 8)
    lines = _wrap_text_block(pdf, text, pdf.w - 30)
    box_h = max(12, len(lines) * 4.5 + 8)

    if start_y + box_h > pdf.h - 25:
        pdf.add_page()
        start_y = pdf.get_y()

    # Background
    pdf.set_fill_color(*bg)
    pdf.rect(10, start_y, pdf.w - 20, box_h, "F")
    # Left accent bar
    pdf.set_fill_color(*accent)
    pdf.rect(10, start_y, 3, box_h, "F")

    # Title
    pdf.set_font(pdf.fn, "B", 7)
    pdf.set_text_color(*accent)
    pdf.set_xy(16, start_y + 2)
    pdf.cell(0, 4, title)

    # Body text
    pdf.set_font(pdf.fn, "", 8)
    pdf.set_text_color(*_BODY_COLOR)
    for i, line in enumerate(lines):
        pdf.set_xy(16, start_y + 7 + i * 4.5)
        pdf.cell(pdf.w - 30, 4, line)

    pdf.set_y(start_y + box_h + 3)


# -- Executive summary card -------------------------------------------------

def _render_exec_summary(pdf: _BrandedPDF, points: list[str]) -> None:
    """Render executive summary as a highlighted card with text wrapping."""
    if not points:
        return

    pdf.ln(3)
    line_h = 5
    text_w = pdf.w - 40  # 20 left (10 margin + 5 pad + 5 bullet) + 20 right

    # Pre-wrap all points to calculate real height
    pdf.set_font(pdf.fn, "", 9)
    wrapped_points: list[list[str]] = []
    total_lines = 0
    for point in points:
        lines = _wrap_text(pdf, _strip_inline_md(point), text_w)
        wrapped_points.append(lines)
        total_lines += len(lines)

    box_h = total_lines * line_h + 16
    start_y = pdf.get_y()

    if start_y + box_h > pdf.h - 25:
        pdf.add_page()
        start_y = pdf.get_y()

    # Card background with accent top border
    pdf.set_fill_color(*_ACCENT)
    pdf.rect(10, start_y, pdf.w - 20, 3, "F")
    pdf.set_fill_color(248, 250, 252)
    pdf.rect(10, start_y + 3, pdf.w - 20, box_h - 3, "F")
    # Subtle border
    pdf.set_draw_color(*_TABLE_BORDER)
    pdf.set_line_width(0.2)
    pdf.rect(10, start_y, pdf.w - 20, box_h)

    # Title
    pdf.set_font(pdf.fn, "B", 10)
    pdf.set_text_color(*_ACCENT_DARK)
    pdf.set_xy(15, start_y + 5)
    pdf.cell(0, 5, "EXECUTIVE SUMMARY")

    # Points with proper word wrapping
    pdf.set_font(pdf.fn, "", 9)
    y_off = start_y + 13
    for wrapped_lines in wrapped_points:
        # Bullet
        pdf.set_xy(15, y_off)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(5, line_h, pdf.bullet)
        # First line after bullet
        pdf.set_text_color(*_BODY_COLOR)
        pdf.cell(text_w, line_h, wrapped_lines[0] if wrapped_lines else "")
        y_off += line_h
        # Continuation lines (indented to align with first line text)
        for cont_line in wrapped_lines[1:]:
            pdf.set_xy(20, y_off)
            pdf.cell(text_w, line_h, cont_line)
            y_off += line_h

    pdf.set_y(start_y + box_h + 4)


# -- Table rendering --------------------------------------------------------

def _measure_col_widths(
    pdf: _BrandedPDF, rows: list[list[str]], usable: float,
) -> list[float]:
    """Calculate column widths proportional to content."""
    if not rows:
        return []
    max_cols = max(len(r) for r in rows)
    if max_cols == 0:
        return []

    max_w: list[float] = [0.0] * max_cols
    for row in rows:
        for ci, cell in enumerate(row):
            pdf.set_font(pdf.fn, "B", 8)
            sw = pdf.get_string_width(_strip_inline_md(cell)) + 4
            max_w[ci] = max(max_w[ci], sw)

    total = sum(max_w) or 1
    min_col = 18
    max_col = usable * 0.5
    widths = [max(min_col, min(max_col, (w / total) * usable)) for w in max_w]
    scale = usable / (sum(widths) or 1)
    return [w * scale for w in widths]


def _wrap_text(pdf: _BrandedPDF, text: str, width: float) -> list[str]:
    """Word-wrap text to fit within width."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        test = current + " " + word
        if pdf.get_string_width(test) <= width - 3:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _wrap_text_block(pdf: _BrandedPDF, text: str, width: float) -> list[str]:
    """Wrap a block of text into lines."""
    return _wrap_text(pdf, text, width)


def _render_table(pdf: _BrandedPDF, rows: list[list[str]]) -> None:
    """Render table with auto-sized columns, word-wrap, and severity badges."""
    if not rows:
        return

    max_cols = max(len(r) for r in rows)
    usable = pdf.w - 20
    col_widths = _measure_col_widths(pdf, rows, usable)
    cell_h = 5.5
    pad = 1.5

    for row_idx, row in enumerate(rows):
        is_header = row_idx == 0
        pdf.set_font(pdf.fn, "B" if is_header else "", 8)

        wrapped: list[list[str]] = []
        for ci in range(max_cols):
            text = _strip_inline_md(row[ci]) if ci < len(row) else ""
            lines = _wrap_text(pdf, text, col_widths[ci]) if col_widths[ci] > 0 else [text]
            wrapped.append(lines)
        row_h = max(len(lines) for lines in wrapped) * cell_h + pad

        if pdf.get_y() + row_h > pdf.h - 25:
            pdf.add_page()

        start_y = pdf.get_y()

        for ci in range(max_cols):
            x = 10 + sum(col_widths[:ci])
            w = col_widths[ci]

            if is_header:
                pdf.set_fill_color(*_TABLE_HEADER_BG)
            elif row_idx % 2 == 0:
                pdf.set_fill_color(*_TABLE_ALT_BG)
            else:
                pdf.set_fill_color(*_WHITE)
            pdf.rect(x, start_y, w, row_h, "F")

            pdf.set_draw_color(*_TABLE_BORDER)
            pdf.set_line_width(0.15)
            pdf.rect(x, start_y, w, row_h)

            cell_text = _strip_inline_md(row[ci]) if ci < len(row) else ""

            if not is_header and _draw_badge(pdf, cell_text, x, start_y + pad / 2, w, row_h - pad):
                continue

            if is_header:
                pdf.set_font(pdf.fn, "B", 8)
                pdf.set_text_color(*_TABLE_HEADER_FG)
            else:
                pdf.set_font(pdf.fn, "", 8)
                pdf.set_text_color(*_BODY_COLOR)

            lines = wrapped[ci] if ci < len(wrapped) else [""]
            for li, line in enumerate(lines):
                pdf.set_xy(x + 2, start_y + pad / 2 + li * cell_h)
                pdf.cell(w - 4, cell_h, line)

        pdf.set_y(start_y + row_h)

    pdf.ln(3)


# -- Auto-chart detection ---------------------------------------------------

def _extract_numeric(text: str) -> float | None:
    """Try to extract a numeric value from text."""
    cleaned = re.sub(r"[,$%\s]", "", _strip_inline_md(text))
    try:
        return float(cleaned)
    except ValueError:
        return None


# Severity/priority keywords mapped to numeric scores for charting
_SEVERITY_SCORES: dict[str, float] = {
    "critical": 4, "kriticka": 4, "kriticky": 4, "kriticke": 4,
    "high": 3, "vysoka": 3, "vysoky": 3, "vysoke": 3,
    "medium": 2, "stredni": 2, "prumerna": 2, "prumerny": 2,
    "low": 1, "nizka": 1, "nizky": 1, "nizke": 1,
    "p0": 4, "p1": 3, "p2": 2, "p3": 1,
    "very high": 4, "velmi vysoka": 4,
}


def _extract_severity_score(text: str) -> float | None:
    """Map severity/priority keywords to numeric scores."""
    key = re.sub(r"[^a-z0-9 ]", "", _strip_inline_md(text).lower().strip())
    # Try exact match first, then without spaces
    return _SEVERITY_SCORES.get(key) or _SEVERITY_SCORES.get(key.replace(" ", ""))


def _auto_chart_table(
    chart_gen: _ChartGen, pdf: _BrandedPDF, rows: list[list[str]],
) -> None:
    """Auto-generate chart from table data if numeric or severity columns detected."""
    if not HAS_MATPLOTLIB or len(rows) < 3:
        return

    header = rows[0]
    data_rows = rows[1:]
    n_cols = len(header)

    # Find numeric columns
    numeric_cols: list[int] = []
    for ci in range(n_cols):
        count = sum(
            1 for row in data_rows
            if ci < len(row) and _extract_numeric(row[ci]) is not None
        )
        if count >= len(data_rows) * 0.6:
            numeric_cols.append(ci)

    # Find severity/priority columns (keywords like Critical, High, P0, etc.)
    severity_cols: list[int] = []
    if not numeric_cols:
        for ci in range(n_cols):
            count = sum(
                1 for row in data_rows
                if ci < len(row) and _extract_severity_score(row[ci]) is not None
            )
            if count >= len(data_rows) * 0.5:
                severity_cols.append(ci)

    if not numeric_cols and not severity_cols:
        return

    # Find label column (first non-numeric, non-severity)
    used_cols = set(numeric_cols) | set(severity_cols)
    label_col = next((ci for ci in range(n_cols) if ci not in used_cols), None)
    if label_col is None:
        return

    labels = [
        _strip_inline_md(row[label_col])[:25] if label_col < len(row) else ""
        for row in data_rows
    ]

    # --- Severity-based chart ---
    if severity_cols and not numeric_cols:
        ci0 = severity_cols[0]
        values = [
            _extract_severity_score(row[ci0]) or 0
            for row in data_rows if ci0 < len(row)
        ]
        if len(values) != len(labels):
            values = values[:len(labels)]
        title = _strip_inline_md(header[ci0])
        img = chart_gen.horizontal_bars(labels, values, title=title)
        if img:
            _embed_chart(pdf, img)
        return

    # --- Numeric chart ---
    if len(numeric_cols) == 1:
        # Single numeric column -> horizontal bar chart
        ci0 = numeric_cols[0]
        values = [
            _extract_numeric(row[ci0]) or 0
            for row in data_rows if ci0 < len(row)
        ]
        if len(values) != len(labels):
            values = values[:len(labels)]
        title = _strip_inline_md(header[numeric_cols[0]])
        img = chart_gen.horizontal_bars(labels, values, title=title)
        if img:
            _embed_chart(pdf, img)

    elif len(numeric_cols) >= 2 and len(data_rows) >= 3:
        # Multiple numeric columns - check if radar makes sense
        categories = [_strip_inline_md(header[ci]) for ci in numeric_cols]
        series: dict[str, list[float]] = {}
        all_vals: list[float] = []
        for row in data_rows:
            name = _strip_inline_md(row[label_col])[:20] if label_col < len(row) else "?"
            vals = [_extract_numeric(row[ci]) or 0 for ci in numeric_cols]
            series[name] = vals
            all_vals.extend(v for v in vals if v > 0)

        if len(series) > 6:
            return

        # Radar only when values are in similar ranges (max/min < 10x)
        min_v = min(all_vals) if all_vals else 0
        max_v = max(all_vals) if all_vals else 0
        scale_ratio = (max_v / min_v) if min_v > 0 else 999

        if scale_ratio < 10:
            img = chart_gen.radar(categories, series)
            if img:
                _embed_chart(pdf, img)
        else:
            # Fallback: bar chart using first numeric column
            values = [
                _extract_numeric(row[numeric_cols[0]]) or 0
                for row in data_rows
            ]
            title = _strip_inline_md(header[numeric_cols[0]])
            img = chart_gen.horizontal_bars(labels, values, title=title)
            if img:
                _embed_chart(pdf, img)


def _embed_chart(pdf: _BrandedPDF, img_path: str) -> None:
    """Embed a chart image into the PDF."""
    chart_w = min(pdf.w - 30, 140)
    x = (pdf.w - chart_w) / 2

    if pdf.get_y() + 70 > pdf.h - 25:
        pdf.add_page()

    pdf.ln(2)
    pdf.image(img_path, x=x, w=chart_w)
    pdf.ln(4)


# -- KPI marker parser ------------------------------------------------------

_KPI_PATTERN = re.compile(
    r"<!--\s*kpi:\s*(.+?)\s*-->", re.IGNORECASE,
)


def _parse_kpi_markers(line: str) -> list[tuple[str, str, str, bool]] | None:
    """Parse KPI markers like <!-- kpi: Revenue=$2.4M(+12%)|NPS=72(+5pts) -->."""
    m = _KPI_PATTERN.match(line.strip())
    if not m:
        return None

    metrics: list[tuple[str, str, str, bool]] = []
    parts = m.group(1).split("|")
    for part in parts:
        part = part.strip()
        eq = part.find("=")
        if eq < 0:
            continue
        label = part[:eq].strip()
        rest = part[eq + 1:].strip()

        change_m = re.search(r"\(([^)]+)\)", rest)
        value = rest[:change_m.start()].strip() if change_m else rest
        change = change_m.group(1) if change_m else ""
        positive = not change.startswith("-")
        metrics.append((label, value, change, positive))

    return metrics if metrics else None


# -- Callout detection ------------------------------------------------------

def _detect_callout(line: str) -> tuple[str, str] | None:
    """Detect GitHub-style callout syntax. Returns (level, text) or None."""
    stripped = line.strip()
    if not stripped.startswith(">"):
        return None
    content = stripped.lstrip(">").strip()

    for marker, level in [
        ("[!IMPORTANT]", "important"), ("[!WARNING]", "warning"),
        ("[!NOTE]", "note"), ("[!TIP]", "note"),
        ("**Important:**", "important"), ("**Warning:**", "warning"),
        ("**Note:**", "note"),
    ]:
        if content.startswith(marker):
            text = content[len(marker):].strip()
            return level, text

    return None


# -- Markdown renderer ------------------------------------------------------

def _render_markdown(
    pdf: _BrandedPDF, markdown: str, chart_gen: _ChartGen,
) -> None:
    """Parse markdown and render into the PDF with modern visual elements."""
    lines = markdown.split("\n")
    fn = pdf.fn
    mn = pdf.mn
    in_code_block = False
    in_table = False
    table_rows: list[list[str]] = []
    exec_summary_points: list[str] = []
    in_exec_summary = False
    first_h1_done = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # KPI marker detection
        kpi_metrics = _parse_kpi_markers(stripped)
        if kpi_metrics:
            _render_kpi_tiles(pdf, kpi_metrics)
            i += 1
            continue

        # Code block toggle
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                pdf.ln(2)
            else:
                in_code_block = True
                pdf.ln(1)
                pdf.set_fill_color(243, 244, 246)
                y = pdf.get_y()
                pdf.rect(10, y, pdf.w - 20, 0.3, "F")
                pdf.ln(1)
            i += 1
            continue

        if in_code_block:
            pdf.set_font(mn, size=7)
            pdf.set_fill_color(248, 249, 250)
            pdf.set_text_color(55, 65, 81)
            pdf.set_x(12)
            pdf.cell(pdf.w - 24, 4, line, fill=True, new_x="LMARGIN", new_y="NEXT")
            i += 1
            continue

        # Empty line
        if not stripped:
            if in_table and table_rows:
                _render_table(pdf, table_rows)
                _auto_chart_table(chart_gen, pdf, table_rows)
                table_rows = []
                in_table = False
            if in_exec_summary and exec_summary_points:
                _render_exec_summary(pdf, exec_summary_points)
                exec_summary_points = []
                in_exec_summary = False
            pdf.ln(2)
            i += 1
            continue

        # Table row
        if "|" in stripped and stripped.startswith("|"):
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                i += 1
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            table_rows.append(cells)
            in_table = True
            i += 1
            continue
        elif in_table and table_rows:
            _render_table(pdf, table_rows)
            _auto_chart_table(chart_gen, pdf, table_rows)
            table_rows = []
            in_table = False

        # Callout detection
        callout = _detect_callout(stripped)
        if callout:
            level, text = callout
            # Collect multi-line callout
            while i + 1 < len(lines) and lines[i + 1].strip().startswith(">"):
                i += 1
                extra = lines[i].strip().lstrip(">").strip()
                if extra:
                    text += " " + extra
            _render_callout(pdf, text, level)
            i += 1
            continue

        # Block quote (not callout)
        if stripped.startswith(">"):
            text = stripped.lstrip(">").strip()
            pdf.ln(1)
            y = pdf.get_y()
            # Left bar quote style
            pdf.set_fill_color(*_ACCENT_LIGHT)
            pdf.rect(10, y, pdf.w - 20, 8, "F")
            pdf.set_fill_color(*_ACCENT)
            pdf.rect(10, y, 2.5, 8, "F")
            pdf.set_font(fn, "I", 9)
            pdf.set_text_color(*_H3_COLOR)
            pdf.set_xy(16, y + 1.5)
            pdf.multi_cell(pdf.w - 30, _LINE_H, _strip_inline_md(text))
            pdf.set_y(max(pdf.get_y(), y + 9))
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^-{3,}$|^\*{3,}$|^_{3,}$", stripped):
            pdf.ln(3)
            pdf.set_draw_color(*_ACCENT)
            pdf.set_line_width(0.4)
            y = pdf.get_y()
            pdf.line(10, y, pdf.w - 10, y)
            pdf.ln(5)
            i += 1
            continue

        # H1
        if stripped.startswith("# ") and not stripped.startswith("## "):
            if in_exec_summary and exec_summary_points:
                _render_exec_summary(pdf, exec_summary_points)
                exec_summary_points = []
                in_exec_summary = False

            if first_h1_done:
                pdf.ln(4)
            pdf.set_font(fn, "B", 20)
            pdf.set_text_color(*_H1_COLOR)
            pdf.multi_cell(0, 10, _strip_inline_md(stripped[2:]))
            # Accent underline
            pdf.set_draw_color(*_ACCENT)
            pdf.set_line_width(1.0)
            y = pdf.get_y() + 1
            pdf.line(10, y, 65, y)
            pdf.ln(6)
            first_h1_done = True
            i += 1
            continue

        # H2 - check for executive summary
        if stripped.startswith("## ") and not stripped.startswith("### "):
            if in_exec_summary and exec_summary_points:
                _render_exec_summary(pdf, exec_summary_points)
                exec_summary_points = []
                in_exec_summary = False

            h2_text = _strip_inline_md(stripped[3:])
            h2_lower = h2_text.lower()

            # Detect executive summary section
            if any(kw in h2_lower for kw in [
                "executive summary", "shrnuti", "souhrn", "key findings",
                "klicove zaver", "hlavni zjisteni",
            ]):
                in_exec_summary = True
                i += 1
                continue

            pdf.ln(6)
            y = pdf.get_y()
            # Section background strip
            pdf.set_fill_color(248, 250, 252)
            pdf.rect(10, y, pdf.w - 20, 10, "F")
            # Left accent bar
            pdf.set_fill_color(*_ACCENT)
            pdf.rect(10, y, 3, 10, "F")
            pdf.set_xy(16, y + 1)
            pdf.set_font(fn, "B", 13)
            pdf.set_text_color(*_H2_COLOR)
            pdf.cell(0, 8, h2_text)
            pdf.ln(13)
            i += 1
            continue

        # H3
        if stripped.startswith("### ") and not stripped.startswith("#### "):
            if in_exec_summary and exec_summary_points:
                _render_exec_summary(pdf, exec_summary_points)
                exec_summary_points = []
                in_exec_summary = False

            pdf.ln(4)
            pdf.set_font(fn, "B", 11)
            pdf.set_text_color(*_H3_COLOR)
            pdf.multi_cell(0, 6, _strip_inline_md(stripped[4:]))
            # Subtle underline
            pdf.set_draw_color(*_TABLE_BORDER)
            pdf.set_line_width(0.3)
            y = pdf.get_y() + 0.5
            pdf.line(10, y, pdf.w * 0.4, y)
            pdf.ln(3)
            i += 1
            continue

        # H4 - colored chip
        if stripped.startswith("#### "):
            pdf.ln(2)
            text = _strip_inline_md(stripped[5:])
            pdf.set_fill_color(*_ACCENT_LIGHT)
            pdf.set_font(fn, "B", 9)
            pdf.set_text_color(*_ACCENT_DARK)
            tw = pdf.get_string_width(text) + 8
            y = pdf.get_y()
            pdf.rect(10, y, min(tw, pdf.w - 20), 6.5, "F")
            pdf.set_xy(12, y + 0.3)
            pdf.cell(min(tw - 4, pdf.w - 24), 6, text)
            pdf.ln(9)
            i += 1
            continue

        # Executive summary bullet collection
        if in_exec_summary:
            bullet_m = re.match(r"^\s*[*\-+]\s+(.*)", stripped)
            num_m = re.match(r"^\s*\d+[.)]\s+(.*)", stripped)
            if bullet_m:
                exec_summary_points.append(bullet_m.group(1))
                i += 1
                continue
            elif num_m:
                exec_summary_points.append(num_m.group(1))
                i += 1
                continue
            elif stripped:
                exec_summary_points.append(stripped)
                i += 1
                continue

        # Bullet points
        bullet_match = re.match(r"^(\s*)[*\-+]\s+(.*)", line)
        if bullet_match:
            indent = len(bullet_match.group(1)) // 2
            text = bullet_match.group(2)
            pdf.set_font(fn, size=9)
            x_off = 14 + indent * 8
            pdf.set_x(x_off)
            pdf.set_text_color(*_ACCENT)
            pdf.cell(5, _LINE_H, pdf.bullet)
            pdf.set_text_color(*_BODY_COLOR)
            pdf.multi_cell(pdf.w - x_off - 15, _LINE_H, _strip_inline_md(text))
            i += 1
            continue

        # Numbered list
        num_match = re.match(r"^(\s*)\d+[.)]\s+(.*)", line)
        if num_match:
            indent = len(num_match.group(1)) // 2
            text = num_match.group(2)
            num_label = re.match(r"(\d+[.)])", stripped)
            pdf.set_font(fn, size=9)
            x_off = 14 + indent * 8
            pdf.set_x(x_off)
            pdf.set_text_color(*_ACCENT)
            pdf.set_font(fn, "B", 9)
            pdf.cell(8, _LINE_H, num_label.group(1) if num_label else "1.")
            pdf.set_text_color(*_BODY_COLOR)
            pdf.set_font(fn, "", 9)
            pdf.multi_cell(pdf.w - x_off - 18, _LINE_H, _strip_inline_md(text))
            i += 1
            continue

        # Regular paragraph
        pdf.set_font(fn, size=9)
        pdf.set_text_color(*_BODY_COLOR)
        pdf.multi_cell(0, _LINE_H, _strip_inline_md(stripped))
        i += 1

    # Flush remaining
    if in_exec_summary and exec_summary_points:
        _render_exec_summary(pdf, exec_summary_points)
    if table_rows:
        _render_table(pdf, table_rows)
        _auto_chart_table(chart_gen, pdf, table_rows)


# -- Public API -------------------------------------------------------------

def generate_branded_pdf(
    markdown_text: str,
    output_path: str | Path,
    language: str = "en",
) -> Path:
    """Generate a branded PDF from markdown text.

    Args:
        markdown_text: Markdown content to render.
        output_path: Where to save the PDF file.
        language: Language code (for header metadata).

    Returns:
        Path to the generated PDF file.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    pdf = _BrandedPDF(language=language)
    pdf.alias_nb_pages()
    pdf.add_page()

    chart_gen = _ChartGen()
    try:
        _render_markdown(pdf, markdown_text, chart_gen)
        pdf.output(str(path))
    finally:
        chart_gen.cleanup()

    return path
