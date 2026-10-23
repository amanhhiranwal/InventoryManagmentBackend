"""The proforma invoice as a PDF, ready to attach to an email.

Drawn to the business's printed Tax Invoice form, line for line the same as
the document the app previews and prints (ProformaInvoiceDocument.tsx): one
ruled box holding the seller and the two parties on the left, the voucher
grid on the right, the goods with their tax lines under them, the HSN-wise
summary, and the bank details beside the terms and the declaration.

One A4 page. An order long enough to need more carries its goods on to the
next page under a repeated column header, with the totals on the last.

Drawn on the canvas rather than flowed with Platypus, because the form is a
fixed grid: the goods' column rules run the full height of the body however
few lines there are, and the foot always sits at the bottom of the page.
"""

from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from app.services.quotation_pdf_service import _words

PAGE_W, PAGE_H = A4

#: Page margin, the same 8mm the browser prints with.
M = 8
W = 194

#: Where the ruled box starts and stops on every page, from the top.
BOX_TOP = 15
BOX_BOTTOM = 283

#: The goods: Sl No. | Description | HSN/SAC | Quantity | Rate | per | Amount.
GOODS = [7, 83, 21, 21, 21, 11, 30]
GOODS_HEAD = 7.5

LEFT_W = W * 0.55
RIGHT_W = W - LEFT_W
CELL_H = 10.5

#: px on the screen document, as points here.
PT = 0.75

REGULAR = "Helvetica"
BOLD = "Helvetica-Bold"
ITALIC = "Helvetica-Oblique"
BOLD_ITALIC = "Helvetica-BoldOblique"


# ----------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------
def _inr(value) -> str:
    """1,74,576.00 - grouped in lakhs and crores, as the form prints it."""

    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        amount = 0.0

    sign = "-" if amount < 0 else ""
    rupees, paise = f"{abs(amount):.2f}".split(".")

    if len(rupees) > 3:
        head, tail = rupees[:-3], rupees[-3:]
        groups = []

        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]

        if head:
            groups.insert(0, head)

        rupees = ",".join(groups) + "," + tail

    return f"{sign}{rupees}.{paise}"


def _quantity(value: float) -> str:
    """2.0 Nos, as the form counts."""

    text = f"{value:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _in_words(value) -> str:
    words = _words(value)
    return "INR " + words[len("Rupees "):] if words.startswith("Rupees ") else words


def _short_date(value) -> str:
    """17-Oct-26."""

    if not value:
        return ""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""

    if isinstance(value, (datetime, date)):
        return value.strftime("%d-%b-%y")

    return ""


def _address_lines(address) -> list[str]:
    """The same two lines the screen document prints."""

    if not isinstance(address, dict):
        return []

    place = ", ".join(p for p in (address.get("city"), address.get("state")) if p)
    second = " ".join(p for p in (place, address.get("pin")) if p)

    return [line for line in (address.get("street"), second) if line]


# ----------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------
class _Page:
    """The canvas, addressed in millimetres from the top-left corner."""

    def __init__(self, c: canvas.Canvas):
        self.c = c

    def y(self, top: float) -> float:
        return PAGE_H - top * mm

    def hline(self, x1: float, x2: float, top: float) -> None:
        self.c.line(x1 * mm, self.y(top), x2 * mm, self.y(top))

    def vline(self, x: float, top1: float, top2: float) -> None:
        self.c.line(x * mm, self.y(top1), x * mm, self.y(top2))

    def text(
        self,
        x: float,
        baseline: float,
        value: str,
        size: float,
        font: str = REGULAR,
        align: str = "left",
    ) -> None:
        self.c.setFont(font, size * PT)

        if align == "right":
            self.c.drawRightString(x * mm, self.y(baseline), value)
        elif align == "center":
            self.c.drawCentredString(x * mm, self.y(baseline), value)
        else:
            self.c.drawString(x * mm, self.y(baseline), value)

    def width(self, value: str, size: float, font: str = REGULAR) -> float:
        return self.c.stringWidth(value, font, size * PT) / mm

    def para(self, para: Paragraph, x: float, top: float, width: float) -> float:
        """Draws a wrapped paragraph; returns the height it took, in mm."""

        _, height = para.wrap(width * mm, PAGE_H)
        para.drawOn(self.c, x * mm, self.y(top) - height)
        return height / mm

    def rupee(self, x: float, baseline: float, size: float) -> float:
        """The ₹ sign, drawn: the built-in fonts have no glyph for it.

        Returns the width it took, in mm.
        """

        s = size * PT
        x0, b = x * mm, self.y(baseline)
        c = self.c

        c.saveState()
        c.setLineWidth(s * 0.085)
        c.line(x0, b + s * 0.70, x0 + s * 0.52, b + s * 0.70)
        c.line(x0, b + s * 0.53, x0 + s * 0.52, b + s * 0.53)

        path = c.beginPath()
        path.moveTo(x0 + s * 0.06, b + s * 0.70)
        path.curveTo(
            x0 + s * 0.46, b + s * 0.70,
            x0 + s * 0.46, b + s * 0.33,
            x0 + s * 0.06, b + s * 0.33,
        )
        path.lineTo(x0 + s * 0.42, b)
        c.drawPath(path, stroke=1, fill=0)
        c.restoreState()

        return (s * 0.62) / mm


def _style(size: float, font: str = REGULAR, leading: float | None = None) -> ParagraphStyle:
    return ParagraphStyle(
        "pi",
        fontName=font,
        fontSize=size * PT,
        leading=(leading or size * 1.36) * PT,
        alignment=TA_LEFT,
    )


def _p(text: str, size: float, font: str = REGULAR, leading: float | None = None) -> Paragraph:
    return Paragraph(escape(text or ""), _style(size, font, leading))


# ----------------------------------------------------------------------
# The document
# ----------------------------------------------------------------------
class ProformaInvoicePDFService:
    @staticmethod
    def filename(invoice) -> str:
        reference = invoice.pi_number or f"PI-{invoice.id}"
        return f"Proforma_Invoice_{reference}.pdf"

    @staticmethod
    def render(invoice, db=None) -> bytes:
        # Read through the same serializer the screen reads, so the PDF is
        # built from exactly the figures the preview shows - the tax summary
        # in particular is worked out there and nowhere else.
        from app.services.proforma_invoice_service import (
            ProformaInvoiceService,
            serialize_proforma_invoice,
        )

        data = serialize_proforma_invoice(invoice)
        profile = ProformaInvoiceService.company_profile(db)

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.setTitle(ProformaInvoicePDFService.filename(invoice)[:-4])

        _Document(_Page(c), data, profile).draw()

        c.save()
        return buffer.getvalue()


class _Document:
    def __init__(self, page: _Page, data: dict, profile: dict):
        self.page = page
        self.data = data
        self.profile = profile or {}
        self.summary = data.get("tax_summary") or None

        info = data.get("customer_information") or {}
        contact = info.get("primary_contact") or {}

        self.contact = "  ".join(p for p in (contact.get("name"), contact.get("phone")) if p)
        self.gstin = info.get("gst")
        self.party = data.get("company_name") or data.get("customer_name") or "-"
        self.reference = data.get("pi_number") or f"PI-{data.get('id')}"

        self.lines = []

        for item in data.get("items") or []:
            qty = float(item.get("qty") or item.get("quantity_case") or 0)
            rate = float(item.get("rate") or item.get("price") or 0) * (
                1 - float(item.get("discount") or 0) / 100
            )
            self.lines.append((item, qty, rate, qty * rate))

        self.charges = self._charges()

    # ------------------------------------------------------------------
    def _charges(self) -> list[tuple[str, float]]:
        """Everything between the goods and the total, as the screen lists it."""

        data = self.data
        goods = sum(line[3] for line in self.lines)
        net = (
            float(data.get("total_amount") or 0)
            - float(data.get("discount_amount") or 0)
            - float(data.get("orc_amount") or 0)
        )

        charges: list[tuple[str, float]] = []
        adjustment = net - goods

        if abs(adjustment) >= 0.005:
            charges.append(("Less : Discount" if adjustment < 0 else "Adjustment", adjustment))

        if float(data.get("freight_charges") or 0):
            charges.append(("Freight & Forwarding", float(data["freight_charges"])))

        if float(data.get("installation_lumpsum") or 0):
            charges.append(("Installation & Commissioning", float(data["installation_lumpsum"])))

        summary = self.summary

        if summary:
            if summary.get("interstate"):
                charges.append(("Output IGST", float(summary.get("igst_total") or 0)))
            else:
                charges.append(("Output CGST", float(summary.get("cgst_total") or 0)))
                charges.append(("Output SGST", float(summary.get("sgst_total") or 0)))
        elif float(data.get("gst_amount") or 0):
            charges.append(("Output GST", float(data["gst_amount"])))

        listed = goods + sum(amount for _, amount in charges)
        round_off = float(data.get("grand_total") or 0) - listed

        if abs(round_off) >= 0.005:
            charges.append(("Round Off", round_off))

        return charges

    # ------------------------------------------------------------------
    def draw(self) -> None:
        page = self.page
        page.c.setLineWidth(0.6)

        rows = self._goods_rows()
        bottom_h = self._bottom_height()

        self._frame()
        top = self._head(BOX_TOP)
        body_top = self._goods_header(top)

        cursor = body_top
        index = 0

        while True:
            remaining = sum(h for h, _ in rows[index:])

            # The rest fits above the foot: finish on this page.
            if cursor + remaining <= BOX_BOTTOM - bottom_h:
                for height, draw in rows[index:]:
                    draw(cursor)
                    cursor += height

                body_bottom = BOX_BOTTOM - bottom_h
                self._goods_rules(body_top, body_bottom)
                self._bottom(body_bottom)
                break

            # Otherwise fill this page with whole rows and carry on.
            placed = False

            while index < len(rows) and cursor + rows[index][0] <= BOX_BOTTOM:
                rows[index][1](cursor)
                cursor += rows[index][0]
                index += 1
                placed = True

            self._goods_rules(body_top, BOX_BOTTOM)
            page.text(M + W, BOX_BOTTOM + 4, "continued ...", 10 * 0.9, ITALIC, "right")

            if not placed and index < len(rows):
                # A single row taller than a page: draw it anyway, clipped,
                # rather than loop forever.
                rows[index][1](cursor)
                index += 1

            page.c.showPage()
            page.c.setLineWidth(0.6)
            self._frame()
            body_top = self._goods_header(BOX_TOP)
            cursor = body_top

    # ------------------------------------------------------------------
    def _frame(self) -> None:
        page = self.page
        page.text(M + W / 2, BOX_TOP - 2.2, "Proforma Invoice", 15, BOLD, "center")
        page.c.rect(M * mm, page.y(BOX_BOTTOM), W * mm, (BOX_BOTTOM - BOX_TOP) * mm)
        page.text(M + W / 2, BOX_BOTTOM + 4, "This is a Computer Generated Invoice", 10, REGULAR, "center")

    # ------------------------------------------------------------------
    def _party(
        self,
        top: float,
        title: str | None,
        name: str,
        lines: list[str],
        gstin: str | None,
        state_name: str | None,
        state_code: str | None,
        email: str | None = None,
        contact: str | None = None,
    ) -> float:
        page = self.page
        x = M + 1.5
        width = LEFT_W - 3
        cursor = top + 1

        if title:
            cursor += page.para(_p(title, 10), x, cursor, width)

        cursor += page.para(_p(name, 11.5, BOLD), x, cursor, width)

        extra = list(lines)

        if contact:
            extra.append(contact)
        if gstin:
            extra.append(f"GSTIN/UIN  : {gstin}")
        if state_name or state_code:
            extra.append(
                f"State Name  : {state_name or '-'}"
                + (f", Code : {state_code}" if state_code else "")
            )
        if email:
            extra.append(f"E-Mail : {email}")

        for line in extra:
            cursor += page.para(_p(line, 11, leading=15), x, cursor, width)

        return cursor + 1 - top

    def _head(self, top: float) -> float:
        page = self.page
        data = self.data
        profile = self.profile
        ship = data.get("shipping_address") or {}
        bill = data.get("billing_address") or {}
        place = data.get("place_of_supply")
        code = data.get("buyer_state_code")

        # Left: the seller, then who it goes to, then who pays.
        cursor = top
        cursor += self._party(
            cursor,
            None,
            profile.get("legal_name") or "Company name not configured",
            profile.get("address_lines") or [],
            profile.get("gstin"),
            profile.get("state_name"),
            profile.get("state_code"),
            email=profile.get("email"),
        )
        page.hline(M, M + LEFT_W, cursor)

        cursor += self._party(
            cursor, "Consignee (Ship to)", self.party, _address_lines(ship),
            self.gstin, ship.get("state") or place, code, contact=self.contact,
        )
        page.hline(M, M + LEFT_W, cursor)

        cursor += self._party(
            cursor, "Buyer (Bill to)", self.party, _address_lines(bill),
            self.gstin, bill.get("state") or place, code, contact=self.contact,
        )

        # Right: the voucher grid.
        order = data.get("sales_order") or {}
        cells = [
            (None, None),
            ("Dated", _short_date(data.get("issue_date"))),
            ("Delivery Note", ""),
            ("Mode/Terms of Payment", f"{float(data.get('advance_percent') or 30):g}% Advance"),
            ("Reference No. & Date.", order.get("order_number") or ""),
            ("Other References", ""),
            ("Buyer's Order No.", order.get("po_number") or ""),
            ("Dated", _short_date(order.get("po_date"))),
            ("Dispatch Doc No.", ""),
            ("Delivery Note Date", ""),
            ("Dispatched through", ""),
            ("Destination", ship.get("city") or ""),
            ("Vessel/Flight No.", ""),
            ("Place of receipt by shipper:", ""),
            ("City/Port of Loading", ""),
            ("City/Port of Discharge", ""),
        ]

        grid_h = (len(cells) // 2) * CELL_H
        bottom = max(cursor, top + grid_h + 14)
        rx = M + LEFT_W
        half = RIGHT_W / 2

        for index, (label, value) in enumerate(cells):
            row, col = divmod(index, 2)
            x = rx + col * half
            cell_top = top + row * CELL_H

            if index == 0:
                page.text(x + 1, cell_top + 3.2, "Invoice No.", 10)
                page.text(x + half / 2 + 0.5, cell_top + 3.2, "e-Way Bill No.", 10)
                page.text(x + 1, cell_top + 7.2, self.reference, 11, BOLD)
            else:
                page.text(x + 1, cell_top + 3.2, label, 10)
                if value:
                    page.text(x + 1, cell_top + 7.2, str(value), 11, BOLD)

            page.hline(x, x + half, cell_top + CELL_H)

        page.vline(rx + half, top, top + grid_h)
        page.text(rx + 1, top + grid_h + 3.2, "Terms of Delivery", 10)

        page.vline(rx, top, bottom)
        page.hline(M, M + W, bottom)

        return bottom

    # ------------------------------------------------------------------
    def _columns(self) -> list[float]:
        """The x of each goods column's left edge, and the right edge last."""

        edges = [M]
        for width in GOODS:
            edges.append(edges[-1] + width)
        return edges

    def _goods_header(self, top: float) -> float:
        page = self.page
        edges = self._columns()
        headings = ["Sl", "Description of Goods", "HSN/SAC", "Quantity", "Rate", "per", "Amount"]

        for index, heading in enumerate(headings):
            centre = (edges[index] + edges[index + 1]) / 2
            page.text(centre, top + 3.2, heading, 10, REGULAR, "center")

        page.text((edges[0] + edges[1]) / 2, top + 6.4, "No.", 10, REGULAR, "center")

        for x in edges[1:-1]:
            page.vline(x, top, top + GOODS_HEAD)

        page.hline(M, M + W, top + GOODS_HEAD)
        return top + GOODS_HEAD

    def _goods_rules(self, top: float, bottom: float) -> None:
        for x in self._columns()[1:-1]:
            self.page.vline(x, top, bottom)

    def _goods_rows(self) -> list[tuple[float, callable]]:
        page = self.page
        edges = self._columns()
        desc_x = edges[1] + 1
        desc_w = GOODS[1] - 2
        rows: list[tuple[float, callable]] = []

        for index, (item, qty, rate, amount) in enumerate(self.lines, start=1):
            name = item.get("product") or item.get("description") or item.get("item") or "-"
            details = [item.get("model")]

            if item.get("description") and item.get("description") not in (
                item.get("product"),
                item.get("model"),
            ):
                details.append(item.get("description"))
            if item.get("warranty_term"):
                details.append(f"Warranty : {item['warranty_term']}")

            name_p = _p(name, 11, BOLD)
            detail_ps = [_p(d, 10.5, ITALIC, leading=15) for d in details if d]

            height = name_p.wrap(desc_w * mm, PAGE_H)[1] / mm
            height += sum(p.wrap((desc_w - 8) * mm, PAGE_H)[1] / mm for p in detail_ps)
            height += 2

            def draw(top, index=index, item=item, qty=qty, rate=rate, amount=amount,
                     name_p=name_p, detail_ps=detail_ps):
                baseline = top + 1 + 3
                page.text(edges[1] - 1, baseline, str(index), 11, REGULAR, "right")

                cursor = top + 1
                cursor += page.para(name_p, desc_x, cursor, desc_w)
                for p in detail_ps:
                    cursor += page.para(p, desc_x + 8, cursor, desc_w - 8)

                page.text(edges[2] + 1, baseline, str(item.get("hsn") or ""), 11)
                page.text(edges[4] - 1, baseline, f"{_quantity(qty)} Nos", 11, BOLD, "right")
                page.text(edges[5] - 1, baseline, _inr(rate), 11, REGULAR, "right")
                page.text(edges[5] + 1, baseline, "Nos", 11)
                page.text(edges[7] - 1, baseline, _inr(amount), 11, BOLD, "right")

            rows.append((height, draw))

        for position, (label, amount) in enumerate(self.charges):
            gap = 4 if position == 0 else 0

            def draw(top, label=label, amount=amount, gap=gap):
                baseline = top + gap + 3
                page.text(edges[2] - 2, baseline, label, 11, BOLD_ITALIC, "right")
                page.text(edges[7] - 1, baseline, _inr(amount), 11, BOLD, "right")

            rows.append((gap + 4.2, draw))

        return rows

    # ------------------------------------------------------------------
    # The foot: total, words, HSN table, terms, bank, signature
    # ------------------------------------------------------------------
    TOTAL_H = 5.5
    WORDS_H = 9
    HSN_ROW = 4.2
    TAX_WORDS_H = 6

    def _terms(self) -> list[str]:
        return [str(t).strip() for t in (self.data.get("commercial_terms") or []) if str(t).strip()]

    def _foot_left(self) -> tuple[list[Paragraph], Paragraph]:
        width = W / 2 - 3
        terms = [
            Paragraph(
                f"{index}.&nbsp;&nbsp;{escape(term)}",
                ParagraphStyle(
                    "term", fontName=REGULAR, fontSize=9.5 * PT, leading=13 * PT,
                    leftIndent=3.2 * mm, firstLineIndent=-3.2 * mm,
                ),
            )
            for index, term in enumerate(self._terms(), start=1)
        ]
        for p in terms:
            p.wrap(width * mm, PAGE_H)

        declaration = _p(
            "We declare that invoice shows the actual price of the goods "
            "described and that all particulars are true and correct.",
            10,
            leading=14,
        )
        declaration.wrap(width * mm, PAGE_H)
        return terms, declaration

    def _foot_height(self) -> float:
        terms, declaration = self._foot_left()
        left = 1
        if terms:
            left += 4 + sum(p.height for p in terms) / mm + 0.5 * len(terms)
        left += 3 + 4 + declaration.height / mm + 1

        right = 1 + 4 + 5 * 3.97 + 1 + 20
        return max(left, right)

    def _bottom_height(self) -> float:
        hsn_rows = len((self.summary or {}).get("rows") or [])
        hsn = (3 + hsn_rows) * self.HSN_ROW if hsn_rows else 0
        return self.TOTAL_H + self.WORDS_H + hsn + self.TAX_WORDS_H + self._foot_height()

    def _bottom(self, top: float) -> None:
        page = self.page
        data = self.data
        edges = self._columns()

        # The total row.
        bottom = top + self.TOTAL_H
        page.hline(M, M + W, top)
        page.hline(M, M + W, bottom)
        for x in edges[1:-1]:
            page.vline(x, top, bottom)

        baseline = top + 4
        total_qty = sum(line[1] for line in self.lines)
        page.text(edges[2] - 1, baseline, "Total", 11, REGULAR, "right")
        page.text(edges[4] - 1, baseline, f"{_quantity(total_qty)} Nos", 11, BOLD, "right")

        amount = _inr(data.get("grand_total"))
        amount_w = page.width(amount, 13, BOLD_ITALIC)
        page.text(edges[7] - 1, baseline + 0.3, amount, 13, BOLD_ITALIC, "right")
        page.rupee(edges[7] - 1 - amount_w - 3.6, baseline + 0.3, 13)

        # The total in words.
        cursor = bottom
        page.text(M + 1.5, cursor + 3, "Amount Chargeable (in words)", 10)
        page.text(M + W - 1.5, cursor + 3, "E. & O.E", 10, ITALIC, "right")
        page.text(M + 1.5, cursor + 7.3, _in_words(data.get("grand_total")), 11.5, BOLD)
        cursor += self.WORDS_H

        # The HSN-wise tax.
        if self.summary and self.summary.get("rows"):
            cursor = self._hsn_table(cursor)

        tax_total = (self.summary or {}).get("tax_total", data.get("gst_amount"))
        label = "Tax Amount (in words)  :   "
        page.text(M + 1.5, cursor + 4, label, 10)
        page.text(M + 1.5 + page.width(label, 10), cursor + 4, _in_words(tax_total), 11.5, BOLD)
        cursor += self.TAX_WORDS_H

        self._foot(cursor)

    def _hsn_table(self, top: float) -> float:
        page = self.page
        summary = self.summary
        interstate = bool(summary.get("interstate"))
        row_h = self.HSN_ROW

        # Widths from the right: total tax, then each tax's rate and amount,
        # then the taxable value; HSN takes what is left.
        heads = ["IGST"] if interstate else ["CGST", "SGST/UTGST"]
        right = M + W
        total_x = right - 24
        tax_x = []
        x = total_x

        for _ in reversed(heads):
            x -= 38
            tax_x.insert(0, x)

        taxable_x = x - 24
        rows = summary.get("rows") or []
        bottom = top + (3 + len(rows)) * row_h

        page.hline(M, right, top)
        page.hline(taxable_x + 24, total_x, top + row_h)
        page.hline(M, right, top + 2 * row_h)
        page.hline(M, right, bottom - row_h)
        page.hline(M, right, bottom)

        for edge in [taxable_x, *tax_x, total_x]:
            page.vline(edge, top, bottom)
        for start in tax_x:
            page.vline(start + 14, top + row_h, bottom)

        size = 10
        page.text((M + taxable_x) / 2, top + 5.6, "HSN/SAC", size, REGULAR, "center")
        page.text(taxable_x + 12, top + 3.2, "Taxable", size, REGULAR, "center")
        page.text(taxable_x + 12, top + 7.2, "Value", size, REGULAR, "center")
        page.text(total_x + 12, top + 3.2, "Total", size, REGULAR, "center")
        page.text(total_x + 12, top + 7.2, "Tax Amount", size, REGULAR, "center")

        for head, start in zip(heads, tax_x):
            page.text(start + 19, top + 3.2, head, size, REGULAR, "center")
            page.text(start + 7, top + 7.2, "Rate", size, REGULAR, "center")
            page.text(start + 26, top + 7.2, "Amount", size, REGULAR, "center")

        keys = [("igst_rate", "igst_amount")] if interstate else [
            ("cgst_rate", "cgst_amount"),
            ("sgst_rate", "sgst_amount"),
        ]
        totals = ["igst_total"] if interstate else ["cgst_total", "sgst_total"]

        for index, row in enumerate(rows):
            baseline = top + (2 + index) * row_h + 3.1
            page.text(M + 1, baseline, str(row.get("hsn") or ""), size)
            page.text(taxable_x + 23, baseline, _inr(row.get("taxable")), size, REGULAR, "right")

            for (rate_key, amount_key), start in zip(keys, tax_x):
                rate = float(row.get(rate_key) or 0)
                page.text(start + 7, baseline, f"{rate:g}%", size, REGULAR, "center")
                page.text(start + 37, baseline, _inr(row.get(amount_key)), size, REGULAR, "right")

            page.text(right - 1, baseline, _inr(row.get("total_tax")), size, REGULAR, "right")

        baseline = bottom - row_h + 3.1
        page.text(taxable_x - 1, baseline, "Total", size, BOLD, "right")
        page.text(taxable_x + 23, baseline, _inr(summary.get("taxable_total")), size, BOLD, "right")

        for key, start in zip(totals, tax_x):
            page.text(start + 37, baseline, _inr(summary.get(key)), size, BOLD, "right")

        page.text(right - 1, baseline, _inr(summary.get("tax_total")), size, BOLD, "right")

        return bottom

    def _foot(self, top: float) -> None:
        page = self.page
        profile = self.profile
        bank = profile.get("bank") or {}
        mid = M + W / 2

        # Left: the terms from the sales order, then the declaration at the
        # bottom of the box.
        terms, declaration = self._foot_left()
        cursor = top + 1

        if terms:
            page.text(M + 1.5, cursor + 3, "Terms & Conditions", 10)
            page.hline(M + 1.5, M + 1.5 + page.width("Terms & Conditions", 10), cursor + 3.6)
            cursor += 4

            for p in terms:
                cursor += page.para(p, M + 1.5, cursor, W / 2 - 3) + 0.5

        decl_top = BOX_BOTTOM - 1 - declaration.height / mm - 4
        page.text(M + 1.5, decl_top + 3, "Declaration", 10)
        page.hline(M + 1.5, M + 1.5 + page.width("Declaration", 10), decl_top + 3.6)
        page.para(declaration, M + 1.5, decl_top + 4, W / 2 - 3)

        # Right: the bank, then the signature box to the bottom of the page.
        x = mid + 1.5
        cursor = top + 1
        page.text(x, cursor + 3, "Company's Bank Details", 10.5)
        cursor += 4

        rows = [
            ("A/c Holder's Name", bank.get("beneficiary_name") or profile.get("legal_name")),
            ("Bank Name", bank.get("bank_name")),
            ("A/c No.", bank.get("account_number")),
            ("Branch & IFS Code", " & ".join(p for p in (bank.get("branch"), bank.get("ifsc")) if p)),
            ("SWIFT Code", bank.get("swift")),
        ]

        for label, value in rows:
            page.text(x, cursor + 3, label, 10.5)
            page.text(x + 28, cursor + 3, f": {value or ''}", 10.5, BOLD)
            cursor += 3.97

        sign_top = cursor + 1
        page.hline(mid, M + W, sign_top)
        page.vline(mid, sign_top, BOX_BOTTOM)

        legal = profile.get("legal_name") or ""
        page.text(M + W - 1.5, sign_top + 4, f"for {legal}", 10.5, BOLD, "right")
        page.text(M + W - 1.5, BOX_BOTTOM - 1.5, "Authorised Signatory", 10, REGULAR, "right")
