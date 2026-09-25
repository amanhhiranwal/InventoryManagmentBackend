"""The quotation as a proposal PDF, ready to attach to an email.

Laid out like the business proposal that goes out today: a cover carrying
the logo and a navy sweep, an About page whose text sits in a navy panel
over the range on offer, the priced table under a navy header, and the
terms and signature. Every page after the cover carries the logo and a
navy footer bar with the website.

Built with reportlab's Platypus so it paginates on its own - a quotation
with forty lines spills onto the next page with the table header repeated,
rather than running off the bottom.
"""

from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Frame,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    BaseDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.services.company_profile_service import CompanyProfileService

#: The brand navy, sampled from the printed proposal.
NAVY = colors.HexColor("#1f477b")
INK = colors.HexColor("#1f2d3d")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#c9d2e0")
BAND = colors.HexColor("#f4f7fb")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

#: Sits at the bottom of every page after the cover.
FOOTER_H = 9 * mm

ABOUT_FALLBACK = (
    "A technology hardware and business consulting company delivering "
    "quality services directly and as a partner, helping organizations "
    "connect with their customers. With a range of offerings across "
    "technology, training, automation and support, we adapt to what our "
    "customers need as the landscape changes."
)

DEFAULT_LOGO = Path(__file__).resolve().parents[1] / "assets" / "brand-logo.jpg"


def _money(value) -> str:
    """Indian grouping, as the printed proposal uses: 1,23,456.00."""

    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "0.00"

    whole, decimals = f"{abs(amount):.2f}".split(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail

    return ("-" if amount < 0 else "") + f"{whole}.{decimals}"


def _date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    return "-"


def _address_lines(address) -> list[str]:
    if not isinstance(address, dict):
        return []

    parts = [
        address.get("street"),
        address.get("city"),
        address.get("state"),
        address.get("zipCode") or address.get("zip_code"),
        address.get("country"),
    ]
    return [str(part).strip() for part in parts if str(part or "").strip()]


def _split(raw: str) -> list[str]:
    """A setting written with "|" or newlines, as a list."""

    return [
        piece.strip()
        for piece in (raw or "").replace("|", "\n").splitlines()
        if piece.strip()
    ]


def _logo_path(configured: str | None = None) -> Path | None:
    configured = (configured or settings.COMPANY_LOGO_PATH or "").strip()

    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / configured
        if path.exists():
            return path

    return DEFAULT_LOGO if DEFAULT_LOGO.exists() else None


def _styles() -> dict:
    base = getSampleStyleSheet()

    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"], fontSize=42, leading=46,
            textColor=NAVY, alignment=0, spaceAfter=2,
            fontName="Helvetica-Bold",
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"], fontSize=20, leading=25,
            textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=26,
        ),
        "label": ParagraphStyle(
            "label", parent=base["Normal"], fontSize=10.5, leading=14,
            textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=3,
        ),
        "value": ParagraphStyle(
            "value", parent=base["Normal"], fontSize=10, leading=14,
            textColor=INK,
        ),
        "panel_title": ParagraphStyle(
            "panel_title", parent=base["Normal"], fontSize=17, leading=22,
            textColor=colors.white, fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "panel_body": ParagraphStyle(
            "panel_body", parent=base["Normal"], fontSize=8.4, leading=12.6,
            textColor=colors.white, alignment=TA_JUSTIFY, spaceAfter=7,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Normal"], fontSize=17, leading=22,
            textColor=NAVY, fontName="Helvetica-Bold", alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Normal"], fontSize=11, leading=15,
            textColor=INK, fontName="Helvetica-Bold", spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=9, leading=13.5,
            textColor=INK, spaceAfter=5,
        ),
        "offering": ParagraphStyle(
            "offering", parent=base["Normal"], fontSize=10, leading=17,
            textColor=INK,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontSize=7.8, leading=10.8,
            alignment=TA_CENTER, textColor=INK,
        ),
        "cell_left": ParagraphStyle(
            "cell_left", parent=base["Normal"], fontSize=7.8, leading=10.8,
            textColor=INK,
        ),
        "cell_serial": ParagraphStyle(
            "cell_serial", parent=base["Normal"], fontSize=8, leading=11,
            textColor=colors.white, fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "cell_head": ParagraphStyle(
            "cell_head", parent=base["Normal"], fontSize=8, leading=11,
            textColor=colors.white, fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontSize=8.5, leading=12,
            textColor=MUTED,
        ),
        "sign": ParagraphStyle(
            "sign", parent=base["Normal"], fontSize=9.5, leading=14,
            textColor=INK, fontName="Helvetica-Bold",
        ),
    }


class QuotationPDFService:

    @staticmethod
    def company(db=None, quotation=None, company_id=None) -> dict:
        """Who the proposal comes from.

        The selling company first: one installation can run several, and a
        quotation for Unique Event must not go out under Qonevo's website.
        Anything that company does not carry - the About copy, the range,
        who signs - falls back to the global Company Profile, and that in
        turn falls back to the environment.

        ``company_id`` names the seller directly, for a quotation still
        being typed: the picker has a company but nothing is saved yet, and
        the preview has to follow the picker.
        """

        profile = CompanyProfileService.as_lists(db)
        seller = QuotationPDFService._seller(db, quotation, company_id)

        return {
            "name": (seller and seller.company_name) or profile["company_legal_name"] or "Synergy Group",
            "address": (
                QuotationPDFService._seller_address(seller)
                or profile["company_address_lines"]
            ),
            "gstin": (seller and seller.gst_number) or profile["company_gstin"] or None,
            "website": (seller and seller.website) or profile["company_website"] or None,
            "email": (seller and seller.email) or profile["company_email"] or None,
            "phone": (seller and seller.phone_number) or profile["company_phone"] or None,
            "signatory": profile["signatory_name"] or None,
            "signatory_title": profile["signatory_title"] or None,
            "offerings": profile["company_offering_list"],
            "about_paragraphs": profile["company_about_paragraphs"],
            "logo_path": (
                QuotationPDFService._seller_logo(seller) or profile["company_logo_path"]
            ),
            "cover_image": profile["company_cover_image"],
        }

    @staticmethod
    def sender(db, quotation) -> dict | None:
        """The person sending the quotation.

        Submitted By names them rather than the company's standing
        signatory - a client should see who they have been dealing with,
        and a proposal signed by someone they have never spoken to reads
        as a form letter. The assignee first, then whoever raised it.
        """

        if db is None or quotation is None:
            return None

        for attr in ("assigned_to_id", "creator_id"):
            user_id = getattr(quotation, attr, None)

            if not user_id:
                continue

            try:
                from app.models.user import User

                user = db.query(User).filter(User.id == user_id).first()

                if user is None:
                    continue

                name = f"{user.first_name or ''} {user.last_name or ''}".strip()

                return {
                    "name": name or user.email,
                    "title": (user.roles[0].role_name if user.roles else None),
                    "email": user.email,
                    "phone": user.phone_number,
                }
            except Exception:  # pragma: no cover - fall back to the profile
                return None

        return None

    @staticmethod
    def _seller(db, quotation, company_id=None):
        """The company selling on this quotation, if one is set."""

        if db is None:
            return None

        company_id = company_id or getattr(quotation, "company_id", None)

        if not company_id:
            return None

        try:
            from app.models.company import Company

            return db.query(Company).filter(Company.id == company_id).first()
        except Exception:  # pragma: no cover - fall back to the profile
            return None

    @staticmethod
    def _seller_address(seller) -> list[str]:
        if seller is None:
            return []

        parts = [
            seller.address_line_1,
            seller.address_line_2,
            seller.city,
            seller.state,
            seller.postal_code,
            seller.country,
        ]
        return [str(part).strip() for part in parts if str(part or "").strip()]

    @staticmethod
    def _seller_logo(seller) -> str:
        """The company's own logo, as a path this process can open.

        Stored as a URL on the company record; only the part under uploads/
        is any use here.
        """

        url = (getattr(seller, "logo_url", None) or "").strip() if seller else ""

        if not url:
            return ""

        marker = "/uploads/"
        return "uploads/" + url.split(marker, 1)[1] if marker in url else ""

    @staticmethod
    def filename(quotation) -> str:
        who = (quotation.organization_name or quotation.contact_name or "Client")
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in who).strip()
        return f"{safe or 'Client'}_Proposal_{quotation.quote_number or quotation.id}.pdf"

    # ------------------------------------------------------------------
    # Page furniture
    # ------------------------------------------------------------------
    @staticmethod
    def _logo(canvas, x, y, width, configured=None):
        path = _logo_path(configured)

        if path is None:
            return

        try:
            from reportlab.lib.utils import ImageReader

            reader = ImageReader(str(path))
            iw, ih = reader.getSize()
            height = width * ih / iw
            canvas.drawImage(
                reader, x, y - height, width=width, height=height,
                mask="auto", preserveAspectRatio=True,
            )
        except Exception:  # pragma: no cover - a missing logo is not fatal
            pass

    @staticmethod
    def _cover_page(company, canvas, doc):
        """The navy sweep and the logo, drawn behind the cover's text."""

        canvas.saveState()

        # A large navy disc off the top-right corner, clipped by the page,
        # giving the sweep the printed proposal opens with.
        canvas.setFillColor(NAVY)
        canvas.circle(PAGE_W + 38 * mm, PAGE_H - 4 * mm, 92 * mm, stroke=0, fill=1)

        # And a second from the bottom-left, closing the page.
        canvas.setFillColor(NAVY)
        canvas.circle(-46 * mm, -30 * mm, 78 * mm, stroke=0, fill=1)

        QuotationPDFService._logo(
            canvas, MARGIN, PAGE_H - 14 * mm, 42 * mm, company["logo_path"]
        )
        canvas.restoreState()

    @staticmethod
    def _inner_page(company, canvas, doc):
        """Logo top-right, navy footer bar with the website."""

        canvas.saveState()
        QuotationPDFService._logo(
            canvas, PAGE_W - MARGIN - 34 * mm, PAGE_H - 10 * mm, 34 * mm,
            company["logo_path"],
        )

        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, PAGE_W, FOOTER_H, stroke=0, fill=1)

        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawCentredString(
            PAGE_W / 2,
            FOOTER_H / 2 - 2.5,
            (company["website"] or company["name"]).upper(),
        )
        canvas.restoreState()

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    @staticmethod
    def _cover(quotation, company, sender, s) -> list:
        headline = (
            quotation.opportunity_name
            or (quotation.items or [{}])[0].get("product")
            or "Commercial Proposal"
        )

        submitted_to = [quotation.organization_name or quotation.contact_name or "-"]
        submitted_to += _address_lines(quotation.billing_address)

        if sender:
            submitted_by = [sender["name"]]
            if sender["title"]:
                submitted_by.append(sender["title"])
            submitted_by.append(company["name"])
            submitted_by += [
                line for line in (sender["email"], sender["phone"]) if line
            ]
        else:
            submitted_by = [company["signatory"] or company["name"]]
            if company["signatory"]:
                if company["signatory_title"]:
                    submitted_by.append(company["signatory_title"])
                submitted_by.append(company["name"])

        block = Table(
            [[
                [
                    Paragraph("SUBMITTED TO:", s["label"]),
                    *[Paragraph(line, s["value"]) for line in submitted_to],
                ],
                [
                    Paragraph("SUBMITTED BY:", s["label"]),
                    *[Paragraph(line, s["value"]) for line in submitted_by],
                ],
            ]],
            colWidths=[CONTENT_W * 0.5, CONTENT_W * 0.5],
        )
        block.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
        ]))

        return [
            Spacer(1, 30 * mm),
            Paragraph("PROPOSAL", s["cover_title"]),
            Paragraph(headline, s["cover_sub"]),
            block,
            Spacer(1, 10 * mm),
            *QuotationPDFService._cover_image(company["cover_image"]),
            Paragraph(
                f"Reference {quotation.quote_number or quotation.id} &nbsp;|&nbsp; "
                f"Issued {_date(quotation.quotation_date)} &nbsp;|&nbsp; "
                f"Valid until {_date(quotation.validation_date)}",
                s["small"],
            ),
            NextPageTemplate("inner"),
            PageBreak(),
        ]

    @staticmethod
    def _cover_image(cover_image: str | None) -> list:
        """The picture the printed proposal carries under the addresses.

        Optional: with no COMPANY_COVER_IMAGE set the cover simply runs
        without one rather than showing a gap or a broken frame.
        """

        configured = (cover_image or "").strip()

        if not configured:
            return []

        path = Path(configured)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / configured

        if not path.exists():
            return []

        try:
            from reportlab.lib.utils import ImageReader

            width, height = ImageReader(str(path)).getSize()
            draw_w = CONTENT_W
            draw_h = draw_w * height / width

            # Never so tall that it pushes the reference line off the page.
            cap = 118 * mm
            if draw_h > cap:
                draw_h, draw_w = cap, cap * width / height

            return [Image(str(path), width=draw_w, height=draw_h), Spacer(1, 8 * mm)]
        except Exception:  # pragma: no cover - a bad image is not fatal
            return []

    @staticmethod
    def _about(quotation, company, s) -> list:
        """The navy panel, then what the company offers."""

        paragraphs = company["about_paragraphs"] or [ABOUT_FALLBACK]

        # Two columns of white text inside one navy cell, as the printed
        # proposal sets it - but a single paragraph runs the full width
        # rather than leaving half the panel empty.
        if len(paragraphs) > 1:
            half = (len(paragraphs) + 1) // 2
            body = Table(
                [[
                    [Paragraph(p, s["panel_body"]) for p in paragraphs[:half]],
                    [Paragraph(p, s["panel_body"]) for p in paragraphs[half:]],
                ]],
                colWidths=[(CONTENT_W - 20 * mm) * 0.5] * 2,
            )
            body.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), 7),
                ("LEFTPADDING", (1, 0), (1, -1), 7),
                ("RIGHTPADDING", (1, 0), (-1, -1), 0),
            ]))
            columns = body
        else:
            columns = Paragraph(paragraphs[0], s["panel_body"])

        panel = Table(
            [
                [Paragraph(f"About {company['name']}", s["panel_title"])],
                [columns],
            ],
            colWidths=[CONTENT_W],
        )
        panel.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("LEFTPADDING", (0, 0), (-1, -1), 10 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10 * mm),
            ("TOPPADDING", (0, 0), (0, 0), 9 * mm),
            ("BOTTOMPADDING", (0, 0), (0, 0), 5 * mm),
            ("TOPPADDING", (0, 1), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8 * mm),
            ("ROUNDEDCORNERS", [10, 10, 10, 10]),
        ]))

        flow = [Spacer(1, 12 * mm), panel]

        offerings = company["offerings"] or QuotationPDFService._offerings_from(quotation)

        if offerings:
            rows = []
            for index in range(0, len(offerings), 2):
                pair = offerings[index:index + 2]
                rows.append([
                    Paragraph(f"<bullet>&bull;</bullet> {pair[0]}", s["offering"]),
                    Paragraph(
                        f"<bullet>&bull;</bullet> {pair[1]}" if len(pair) > 1 else "",
                        s["offering"],
                    ),
                ])

            grid = Table(rows, colWidths=[CONTENT_W * 0.5] * 2)
            grid.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))

            flow += [Spacer(1, 12 * mm), grid]

        flow.append(PageBreak())

        return flow

    @staticmethod
    def _offerings_from(quotation) -> list[str]:
        """What is on this quotation, when no range is configured."""

        seen: list[str] = []

        for raw in quotation.items or []:
            item = raw if isinstance(raw, dict) else {}
            name = str(item.get("product") or item.get("model") or "").strip()

            if name and name not in seen:
                seen.append(name)

        return seen

    @staticmethod
    def _offer(quotation, s) -> list:
        header = [
            Paragraph(text, s["cell_head"])
            for text in ("Sr. No", "Category", "Model", "Description", "Qty",
                         "Price", "GST", "Amount")
        ]

        rows = [header]
        gst_percent = float(quotation.gst_percent or 0)

        for index, raw in enumerate(quotation.items or [], start=1):
            item = raw if isinstance(raw, dict) else {}

            quantity = float(item.get("quantity") or 1)
            unit_price = float(item.get("unit_price") or 0)
            discount = float(item.get("discount") or 0)
            tax = float(item.get("tax") if item.get("tax") is not None else gst_percent)

            net = quantity * unit_price * (1 - discount / 100)
            tax_amount = net * tax / 100

            rows.append([
                Paragraph(str(index), s["cell_serial"]),
                Paragraph(str(item.get("product") or "-"), s["cell"]),
                Paragraph(str(item.get("model") or "-"), s["cell"]),
                Paragraph(str(item.get("sku") or item.get("description") or "-"), s["cell_left"]),
                Paragraph(f"{quantity:g}", s["cell"]),
                Paragraph(f"INR {_money(unit_price)}", s["cell"]),
                Paragraph(_money(tax_amount), s["cell"]),
                Paragraph(f"INR {_money(net + tax_amount)}", s["cell"]),
            ])

        if len(rows) == 1:
            rows.append(
                [Paragraph("No items on this quotation.", s["cell"])]
                + [Paragraph("", s["cell"])] * 7
            )

        table = Table(
            rows,
            # Sums to the text area: any wider and the last columns wrap
            # their figures onto a second line.
            colWidths=[11 * mm, 26 * mm, 22 * mm, 40 * mm, 10 * mm, 23 * mm,
                       19 * mm, 23 * mm],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            # The serial column carries the navy down the left edge, as the
            # printed table does.
            ("BACKGROUND", (0, 1), (0, -1), NAVY),
            ("GRID", (0, 0), (-1, -1), 0.5, RULE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))

        customer = [
            Paragraph("To,", s["body"]),
            Paragraph(
                f"<b>{quotation.organization_name or quotation.contact_name or '-'}</b>",
                s["body"],
            ),
        ]

        for line in _address_lines(quotation.billing_address):
            customer.append(Paragraph(line, s["small"]))

        return [
            Spacer(1, 12 * mm),
            Paragraph("Proposal", s["h2"]),
            Spacer(1, 4 * mm),
            *customer,
            Spacer(1, 5 * mm),
            table,
        ]

    @staticmethod
    def _totals(quotation, s) -> list:
        lines = [("Subtotal", quotation.subtotal)]

        # Discount and ORC are internal margin, never shown to a client.
        if quotation.freight_charges:
            lines.append(("Delivery", quotation.freight_charges))
        if quotation.installation_lumpsum:
            lines.append(("Installation", quotation.installation_lumpsum))

        lines.append(("Taxable Amount", quotation.taxable_amount))
        lines.append(
            (f"GST ({float(quotation.gst_percent or 0):g}%)", quotation.gst_amount)
        )

        rows = [
            [Paragraph(label, s["cell_left"]),
             Paragraph(f"INR {_money(value)}", s["cell_left"])]
            for label, value in lines
        ]
        rows.append([
            Paragraph("<b>Grand Total</b>", s["cell_left"]),
            Paragraph(f"<b>INR {_money(quotation.total_payable)}</b>", s["cell_left"]),
        ])

        table = Table(rows, colWidths=[46 * mm, 38 * mm], hAlign="RIGHT")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, RULE),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BACKGROUND", (0, -1), (-1, -1), BAND),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))

        return [Spacer(1, 5 * mm), table]

    @staticmethod
    def payment_terms(quotation) -> list[str]:
        """The payment split in words, reused by the email body."""

        advance = float(quotation.advance_percent or 0)
        total = float(quotation.total_payable or 0)
        advance_amount = float(quotation.advance_amount or (total * advance / 100))

        return [
            f"{advance:g}% advance with the purchase order - "
            f"INR {_money(advance_amount)}",
            f"{100 - advance:g}% against delivery - "
            f"INR {_money(quotation.on_delivery_amount or (total - advance_amount))}",
        ]

    @staticmethod
    def _terms(quotation, s) -> list:
        points = QuotationPDFService.payment_terms(quotation)

        # The validity period belongs on the document itself, not only in
        # the covering email - it is the one term a client checks first.
        points.append(
            f"This offer is valid until {_date(quotation.validation_date)}"
            " and is subject to reconfirmation thereafter."
        )

        for entry in quotation.terms or []:
            if isinstance(entry, dict) and entry.get("checked") and entry.get("label"):
                points.append(str(entry["label"]))

        flow = [
            Paragraph("General Terms &amp; Conditions:", s["h3"]),
            ListFlowable(
                [ListItem(Paragraph(point, s["body"]), leftIndent=14)
                 for point in points],
                bulletType="bullet",
                bulletFontSize=7,
                leftIndent=14,
            ),
        ]

        if quotation.remarks:
            flow += [
                Paragraph("Remarks", s["h3"]),
                Paragraph(str(quotation.remarks), s["body"]),
            ]

        return flow

    @staticmethod
    def _signature(company, sender, s) -> list:
        """Signed by whoever is sending it, over the company's details."""

        block = [Paragraph("Best Regards", s["sign"])]

        name = (sender and sender["name"]) or company["signatory"]
        title = (sender and sender["title"]) or company["signatory_title"]

        if name:
            block.append(Paragraph(name, s["sign"]))
        if title:
            block.append(Paragraph(title, s["sign"]))

        block.append(Paragraph(company["name"], s["sign"]))

        contact = [
            line
            for line in (
                (sender and sender["phone"]) or company["phone"],
                (sender and sender["email"]) or company["email"],
                company["website"],
            )
            if line
        ]

        if contact:
            block.append(Paragraph(" | ".join(contact), s["small"]))

        for line in company["address"][:2]:
            block.append(Paragraph(line, s["small"]))

        if company["gstin"]:
            block.append(Paragraph(f"GSTIN: {company['gstin']}", s["small"]))

        return [Spacer(1, 10 * mm), KeepTogether(block)]

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @staticmethod
    def render(quotation, db=None) -> bytes:
        """The whole proposal as PDF bytes."""

        company = QuotationPDFService.company(db, quotation)
        sender = QuotationPDFService.sender(db, quotation)
        s = _styles()
        buffer = BytesIO()

        document = BaseDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
            title=f"Proposal {quotation.quote_number or quotation.id}",
            author=company["name"],
        )

        cover_frame = Frame(
            MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="cover",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        inner_frame = Frame(
            MARGIN, MARGIN + FOOTER_H, CONTENT_W,
            PAGE_H - 2 * MARGIN - FOOTER_H, id="inner",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )

        document.addPageTemplates([
            PageTemplate(
                id="cover", frames=[cover_frame],
                onPage=lambda canvas, doc: QuotationPDFService._cover_page(
                    company, canvas, doc
                ),
            ),
            PageTemplate(
                id="inner", frames=[inner_frame],
                onPage=lambda canvas, doc: QuotationPDFService._inner_page(
                    company, canvas, doc
                ),
            ),
        ])

        story = [
            *QuotationPDFService._cover(quotation, company, sender, s),
            *QuotationPDFService._about(quotation, company, s),
            *QuotationPDFService._offer(quotation, s),
            *QuotationPDFService._totals(quotation, s),
            *QuotationPDFService._terms(quotation, s),
            *QuotationPDFService._signature(company, sender, s),
        ]

        document.build(story)

        return buffer.getvalue()
