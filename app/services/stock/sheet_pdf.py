"""Bordered sheet PDFs for warehouse documents (GRN, gate pass).

The warehouse keeps these as one bordered grid: the company letterhead,
the document's heading, label/value rows, an item table and sign-off rows.
Sheet builds that grid with reportlab so each document only says what goes
in which row; the PDF is the same sheet the screen shows.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

PAGE_WIDTH = A4[0] - 24 * mm

_BASE = ParagraphStyle("sheet", fontName="Helvetica", fontSize=8.5, leading=11)
_BOLD = ParagraphStyle("sheet-bold", parent=_BASE, fontName="Helvetica-Bold")
_CENTER = ParagraphStyle("sheet-center", parent=_BASE, alignment=1)
_CENTER_BOLD = ParagraphStyle("sheet-center-bold", parent=_BOLD, alignment=1)
_RIGHT = ParagraphStyle("sheet-right", parent=_BASE, alignment=2)
_RIGHT_BOLD = ParagraphStyle("sheet-right-bold", parent=_BOLD, alignment=2)


def _style(bold: bool, align: str) -> ParagraphStyle:
    if align == "center":
        return _CENTER_BOLD if bold else _CENTER
    if align == "right":
        return _RIGHT_BOLD if bold else _RIGHT
    return _BOLD if bold else _BASE


def text(value) -> str:
    """A value as safe paragraph markup, line breaks kept."""

    return escape("" if value is None else str(value)).replace("\n", "<br/>")


class Sheet:
    """A grid of ``len(fractions)`` columns, filled a row at a time."""

    def __init__(self, fractions: list[float]):
        total = sum(fractions)
        self.widths = [PAGE_WIDTH * f / total for f in fractions]
        self.columns = len(fractions)
        self.rows: list[list] = []
        self.commands: list[tuple] = []

    def row(self, cells: list, *, spans: list[tuple[int, int]] = (), bold: bool | set[int] = False, shade: bool = False,
            align: list[str] | None = None, height: float | None = None, markup: bool = False) -> None:
        """One row. ``cells`` has one entry per column ("" under a span);
        ``spans`` are (first, last) column pairs merged into one cell;
        ``bold`` is the whole row, or the set of columns in bold."""

        index = len(self.rows)
        aligns = align or ["left"] * self.columns
        heavy = (lambda i: bold) if isinstance(bold, bool) else (lambda i: i in bold)
        self.rows.append([
            Paragraph(cell if markup else text(cell), _style(heavy(i), aligns[i])) for i, cell in enumerate(cells)
        ])
        for first, last in spans:
            self.commands.append(("SPAN", (first, index), (last, index)))
        if shade:
            self.commands.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#F1F5F9")))
        if height:
            self.commands.append(("ROWHEIGHT_HINT", index, height))

    def full(self, value, *, bold: bool = False, align: str = "left", markup: bool = False, height: float | None = None) -> None:
        """A row that is one cell across the sheet."""

        self.row([value] + [""] * (self.columns - 1), spans=[(0, self.columns - 1)], bold=bold,
                 align=[align] * self.columns, markup=markup, height=height)

    def render(self, title: str) -> bytes:
        heights = [None] * len(self.rows)
        commands = []
        for command in self.commands:
            if command[0] == "ROWHEIGHT_HINT":
                heights[command[1]] = command[2]
            else:
                commands.append(command)

        table = Table(self.rows, colWidths=self.widths, rowHeights=heights, repeatRows=0)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            *commands,
        ]))

        buffer = BytesIO()
        SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
            title=title,
        ).build([table])
        return buffer.getvalue()


def letterhead_markup(letterhead: dict) -> str:
    """The company block: name, address, then GST / phone / email."""

    contact = " | ".join(
        p for p in (
            f"GST No : {letterhead['gst_number']}" if letterhead.get("gst_number") else "",
            letterhead.get("phone_number") or "",
            letterhead.get("email") or "",
        ) if p
    )
    lines = [f"<font size=12><b>{text(str(letterhead.get('company_name') or '').upper())}</b></font>"]
    if letterhead.get("address"):
        lines.append(text(letterhead["address"]))
    if contact:
        lines.append(text(contact))
    return "<br/>".join(lines)


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
