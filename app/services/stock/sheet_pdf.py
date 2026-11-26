"""Warehouse documents (GRN, gate pass) drawn as the Excel sheets they replace.

The warehouse's GRN SHEET and Gate Pass workbooks are the format: the same
columns at the same relative widths, the same merged cells, fonts, fills,
row heights and borders, and the Synergy logo in the letterhead. ExcelGrid
takes that layout as Excel describes it - column widths in Excel character
units, row heights in points, colours as hex - and scales it to an A4 page
the way Excel's fit-to-page printing does, type included.

Excel's Calibri and Arial print as Helvetica (the PDF engine carries no
Calibri); Times New Roman prints as Times.
"""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Table, TableStyle

LOGO = Path(__file__).resolve().parents[2] / "assets" / "sheet-logo.png"
#: The logo's own proportions (611 x 218 in the workbooks).
LOGO_RATIO = 611 / 218

MARGIN = 10 * mm
PAGE_WIDTH = A4[0] - 2 * MARGIN

FONTS = {
    ("sans", False): "Helvetica",
    ("sans", True): "Helvetica-Bold",
    ("serif", False): "Times-Roman",
    ("serif", True): "Times-Bold",
}


def text(value) -> str:
    """A value as safe paragraph markup, line breaks kept."""

    return escape("" if value is None else str(value)).replace("\n", "<br/>")


def excel_px(width: float) -> float:
    """An Excel column width (characters) in screen pixels, as Excel sizes it."""

    return width * 7 + 5


class ExcelGrid:
    """A sheet built row by row, scaled to fit the page width."""

    def __init__(self, widths: list[float]):
        pixels = [excel_px(w) for w in widths]
        points = sum(pixels) * 0.75
        self.scale = min(1.0, PAGE_WIDTH / points)
        self.widths = [p * 0.75 * self.scale for p in pixels]
        self.columns = len(widths)
        self.rows: list[list] = []
        self.heights: list[float | None] = []
        self.commands: list[tuple] = []

    def para(self, value, *, size=11, bold=False, family="sans", align="left", color="#000000", markup=False):
        style = ParagraphStyle(
            "cell",
            fontName=FONTS[(family, bold)],
            fontSize=size * self.scale,
            leading=size * self.scale * 1.18,
            alignment={"left": 0, "center": 1, "right": 2}[align],
            textColor=colors.HexColor(color),
        )
        return Paragraph(value if markup else text(value), style)

    def row(self, cells: list, *, height: float | None = None, spans=(), fills=None, valign="middle"):
        """``cells`` is one entry per column: a dict of para() arguments
        ({"value": ..., "size": ..., ...}), a flowable, or None for empty;
        ``spans`` are (first, last) columns merged; ``fills`` maps a
        column (or a (first, last) range) to a hex colour."""

        index = len(self.rows)
        built = []
        for cell in cells:
            if cell is None:
                built.append("")
            elif isinstance(cell, dict):
                options = dict(cell)
                built.append(self.para(options.pop("value"), **options))
            else:
                built.append(cell)
        self.rows.append(built)
        self.heights.append(height * self.scale if height else None)
        for first, last in spans:
            self.commands.append(("SPAN", (first, index), (last, index)))
        for key, colour in (fills or {}).items():
            first, last = key if isinstance(key, tuple) else (key, key)
            self.commands.append(("BACKGROUND", (first, index), (last, index), colors.HexColor(colour)))
        self.commands.append(("VALIGN", (0, index), (-1, index), {"middle": "MIDDLE", "top": "TOP"}[valign]))
        return index

    def span_rows(self, first_col: int, first_row: int, last_col: int, last_row: int):
        self.commands.append(("SPAN", (first_col, first_row), (last_col, last_row)))

    def letterhead(self, markup: str, *, size: float, family: str, color: str, height: float, logo_width_cols: int = 2):
        """The company block with the logo at its right, as one cell."""

        logo_height = height * self.scale - 6
        logo = Image(str(LOGO), width=logo_height * LOGO_RATIO, height=logo_height) if LOGO.exists() else ""
        logo_width = logo_height * LOGO_RATIO + 6 if logo else 0
        inner = Table(
            [[self.para(markup, size=size, family=family, color=color, markup=True), logo]],
            colWidths=[sum(self.widths) - logo_width - 8, logo_width],
            style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                   ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                   ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("ALIGN", (1, 0), (1, 0), "RIGHT")],
        )
        return inner

    def render(self, title: str) -> bytes:
        table = Table(self.rows, colWidths=self.widths, rowHeights=self.heights)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BOX", (0, 0), (-1, -1), 1.2, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            *self.commands,
        ]))
        buffer = BytesIO()
        SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
            title=title,
        ).build([table])
        return buffer.getvalue()


def letterhead_lines(letterhead: dict) -> list[str]:
    """Name, address, CIN, GST and phone | email - the workbooks' letterhead."""

    contact = " | ".join(p for p in (letterhead.get("phone_number"), letterhead.get("email")) if p)
    return [
        line for line in (
            str(letterhead.get("company_name") or "").upper(),
            letterhead.get("address") or "",
            f"CIN : {letterhead['cin_number']}" if letterhead.get("cin_number") else "",
            f"GST No : {letterhead['gst_number']}" if letterhead.get("gst_number") else "",
            contact,
        ) if line
    ]


def sheet_date(value) -> str:
    """An ISO date or datetime as dd/mm/yyyy, as the sheets print it."""

    if not value:
        return ""
    raw = str(value)
    try:
        year, month, day = raw[:10].split("-")
        return f"{int(day):02d}/{int(month):02d}/{year}"
    except ValueError:
        return raw


def sheet_qty(value) -> str:
    """A quantity without a trailing .0; "-" when there is none yet."""

    if value is None:
        return "-"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def safe_filename(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(value)).strip("-") or "document"
