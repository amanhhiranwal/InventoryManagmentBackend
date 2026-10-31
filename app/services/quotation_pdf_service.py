"""The quotation as a proposal PDF, ready to attach to an email.

Laid out like the business proposal that goes out today. The cover is a
product photograph across the top with the brand mark on it and PROPOSAL
under it, then the About text and tagline, the document details, and who
it is billed to and prepared by. Every page after it is on a pale tint
with the logo and company line at the top over a maroon rule, and the
address and contacts at the foot: the supply summary under a navy
header, the totals with the advance due and the amount in words, the
payment schedule beside the terms, and who issued it.

The logo, the cover logo, the cover photograph and the copy all come from
Masters > Company Profile; the bundled images are only what prints until
somebody uploads their own.

Built with reportlab's Platypus so it paginates on its own - a quotation
with forty lines spills onto the next page with the table header repeated,
rather than running off the bottom.
"""

from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepInFrame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.services.company_profile_service import CompanyProfileService

#: The colours of the printed proposal.
NAVY = colors.HexColor("#233353")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
MAROON = colors.HexColor("#a3324c")
TEAL = colors.HexColor("#4a8190")
LABEL = colors.HexColor("#5b7590")
TINT = colors.HexColor("#eff4f8")
BOX = colors.HexColor("#eef3f7")
PANEL = colors.HexColor("#f6f9fb")
RULE = colors.HexColor("#dde4ea")

PAGE_W, PAGE_H = A4

#: The text column of every page after the cover.
MARGIN = 13 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

#: Where the header ends and the footer begins on those pages.
HEADER_H = 32 * mm
FOOTER_H = 22 * mm

#: The cover's text starts under the photograph and PROPOSAL.
COVER_TEXT_TOP = 170 * mm
COVER_MARGIN = 10 * mm

ABOUT_FALLBACK = (
    "A technology hardware and business consulting company delivering "
    "quality services directly and as a partner, helping organizations "
    "connect with their customers. With a range of offerings across "
    "technology, training, automation and support, we adapt to what our "
    "customers need as the landscape changes."
)

#: Printed until Masters says otherwise, as the business's proposal has them.
TAGLINE_FALLBACK = "Smarter Displays. Better Decisions."
SERVICES_FALLBACK = "INTERACTIVE DISPLAY | EDUCATION TECHNOLOGY | AV SOLUTIONS"

ASSETS = Path(__file__).resolve().parents[1] / "assets"

#: The colour mark at the top of every page after the cover.
DEFAULT_LOGO = ASSETS / "brand-logo.jpg"

#: The white mark printed over the cover photograph.
DEFAULT_COVER_LOGO = ASSETS / "proposal-cover-logo.png"

#: The photograph the cover opens with.
DEFAULT_COVER = ASSETS / "proposal-hero.jpg"


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


#: The Indian system groups in lakh and crore rather than millions, so the
#: words have to be built the same way the digits are.
_ONES = (
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
    "Sixteen", "Seventeen", "Eighteen", "Nineteen",
)
_TENS = (
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
    "Eighty", "Ninety",
)


def _under_thousand(number: int) -> str:
    if number < 20:
        return _ONES[number]

    if number < 100:
        return (_TENS[number // 10] + (" " + _ONES[number % 10] if number % 10 else "")).strip()

    return (
        _ONES[number // 100]
        + " Hundred"
        + (" " + _under_thousand(number % 100) if number % 100 else "")
    )


def _words(value) -> str:
    """The amount as a customer-facing document has to state it.

    A figure in digits can be altered with a pen; the same figure in words
    cannot, which is why an invoice carries both and why they have to be
    built from the same number rather than typed separately.
    """

    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        return "Rupees Zero Only"

    if amount < 0:
        return "Minus " + _words(-amount)

    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))

    if rupees == 0:
        head = "Zero"
    else:
        parts: list[str] = []

        for divisor, label in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
            if rupees >= divisor:
                parts.append(f"{_words_count(rupees // divisor)} {label}")
                rupees %= divisor

        if rupees:
            parts.append(_under_thousand(rupees))

        head = " ".join(parts)

    words = f"Rupees {head}"

    if paise:
        words += f" and {_under_thousand(paise)} Paise"

    return words + " Only"


def _words_count(number: int) -> str:
    """A crore or lakh count can itself run past a thousand."""

    if number >= 1000:
        return (
            f"{_under_thousand(number // 1000)} Thousand"
            + (f" {_under_thousand(number % 1000)}" if number % 1000 else "")
        )

    return _under_thousand(number)


def _without_repeats(lines) -> list[str]:
    """Drop a line that only repeats one already printed.

    A name over a designation that reads the same - "Super Admin" over
    "Super Admin" - makes a reader stop and wonder what they missed. It is
    the same string twice; one of them goes.
    """

    seen: set[str] = set()
    kept: list[str] = []

    for raw in lines:
        value = str(raw or "").strip()

        if not value or value.lower() in seen:
            continue

        seen.add(value.lower())
        kept.append(value)

    return kept


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



def _image_path(configured: str | None, fallback: Path) -> Path | None:
    """An uploaded image if it is still on disk, else the bundled one."""

    configured = (configured or "").strip()

    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / configured
        if path.exists():
            return path

    return fallback if fallback.exists() else None


@lru_cache(maxsize=8)
def _without_white(path: str, modified: float):
    """A logo with its white background made see-through.

    Worked out once per file (and again if it is replaced, which changes
    its modified time) rather than pixel by pixel on every page.
    """

    from PIL import Image as PILImage

    picture = PILImage.open(path).convert("RGBA")
    picture.putdata([
        (r, g, b, 0) if r > 238 and g > 238 and b > 238 else (r, g, b, a)
        for r, g, b, a in picture.getdata()
    ])
    return picture


def _styles() -> dict:
    base = getSampleStyleSheet()

    def style(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    return {
        "about_title": style(
            "about_title", fontName="Helvetica-Bold", fontSize=15.5,
            leading=20, textColor=INK, spaceAfter=7,
        ),
        "about": style(
            "about", fontSize=10.3, leading=16.8, textColor=INK,
            alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "tagline": style(
            "tagline", fontName="Helvetica-Bold", fontSize=10.3, leading=14,
            textColor=INK,
        ),
        "details_title": style(
            "details_title", fontName="Helvetica-Bold", fontSize=7.4,
            leading=10, textColor=NAVY,
        ),
        "details_label": style(
            "details_label", fontName="Helvetica-Bold", fontSize=8.2,
            leading=10.4, textColor=LABEL,
        ),
        "details_value": style(
            "details_value", fontSize=8.2, leading=10.4, textColor=INK,
        ),
        "purpose": style("purpose", fontSize=9.4, leading=12, textColor=INK),
        "party_label": style(
            "party_label", fontName="Helvetica-Bold", fontSize=7,
            leading=9, textColor=MAROON,
        ),
        "party_name": style(
            "party_name", fontName="Helvetica-Bold", fontSize=11.2,
            leading=13.5, textColor=NAVY,
        ),
        "party_strong": style(
            "party_strong", fontName="Helvetica-Bold", fontSize=7.8,
            leading=9.6, textColor=NAVY,
        ),
        "party_line": style("party_line", fontSize=7.8, leading=9.6, textColor=INK),
        "section": style(
            "section", fontName="Helvetica-Bold", fontSize=8.6, leading=11,
            textColor=NAVY, spaceAfter=5,
        ),
        "head": style(
            "head", fontName="Helvetica-Bold", fontSize=7, leading=9,
            textColor=colors.white, alignment=TA_CENTER,
        ),
        "cell": style(
            "cell", fontSize=7.8, leading=10, textColor=INK, alignment=TA_CENTER,
        ),
        "cell_product": style(
            "cell_product", fontName="Helvetica-Bold", fontSize=7.8,
            leading=9.6, textColor=INK,
        ),
        #: Figures stay on one line: "1,54,580.00" over two lines reads
        #: as two numbers.
        "cell_money": style(
            "cell_money", fontSize=7.8, leading=10, textColor=INK,
            alignment=TA_RIGHT, splitLongWords=0,
        ),
        "total_label": style("total_label", fontSize=8.4, leading=10.5, textColor=MUTED),
        "total_value": style(
            "total_value", fontSize=8.4, leading=10.5, textColor=INK, alignment=TA_RIGHT,
        ),
        "grand_label": style(
            "grand_label", fontName="Helvetica-Bold", fontSize=8.8,
            leading=11, textColor=colors.white,
        ),
        "grand_value": style(
            "grand_value", fontName="Helvetica-Bold", fontSize=8.8,
            leading=11, textColor=colors.white, alignment=TA_RIGHT,
        ),
        "advance_label": style(
            "advance_label", fontName="Helvetica-Bold", fontSize=8.4,
            leading=10.5, textColor=NAVY,
        ),
        "advance_value": style(
            "advance_value", fontName="Helvetica-Bold", fontSize=8.4,
            leading=10.5, textColor=NAVY, alignment=TA_RIGHT,
        ),
        "words": style(
            "words", fontName="Helvetica-Bold", fontSize=8.8, leading=11.5,
            textColor=NAVY,
        ),
        "panel_label": style(
            "panel_label", fontName="Helvetica-Bold", fontSize=7.4,
            leading=9.5, textColor=TEAL, spaceAfter=2,
        ),
        "panel_strong": style(
            "panel_strong", fontName="Helvetica-Bold", fontSize=8.2,
            leading=11, textColor=NAVY,
        ),
        "panel_line": style("panel_line", fontSize=8.2, leading=11, textColor=INK),
        "term": style(
            "term", fontSize=7.3, leading=9.4, textColor=INK, leftIndent=7,
            firstLineIndent=-7,
        ),
        "issued": style(
            "issued", fontName="Helvetica-Bold", fontSize=8.4, leading=11,
            textColor=NAVY,
        ),
        "signatory": style(
            "signatory", fontName="Helvetica-Bold", fontSize=8.4, leading=11,
            textColor=MUTED, alignment=TA_RIGHT,
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
        name = (seller and seller.company_name) or profile["company_legal_name"] or "Synergy Group"

        return {
            "name": name,
            # "About Synergy", not "About SYNERGY GLOBAL PRIVATE LIMITED".
            "brand_name": (
                profile["company_brand_name"] or name.split()[0].capitalize()
            ),
            "tagline": profile["company_tagline"] or TAGLINE_FALLBACK,
            "services_line": profile["company_services_line"] or SERVICES_FALLBACK,
            "cover_logo": profile["company_cover_logo"],
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
    def _draw_image(canvas, path, x, top, width=None, height=None, centre=False,
                    clear_white=False):
        """An image hung from its top edge, sized by width or by height.

        ``clear_white`` makes a logo's white background see-through, so a
        JPG mark sits on the tinted page instead of in a white box.

        Returns the height drawn, 0 when there was nothing to draw. A
        missing or unreadable image leaves the page without it rather
        than failing the whole proposal.
        """

        if path is None:
            return 0

        try:
            from reportlab.lib.utils import ImageReader

            if clear_white:
                reader = ImageReader(_without_white(str(path), Path(path).stat().st_mtime))
            else:
                reader = ImageReader(str(path))
            iw, ih = reader.getSize()

            if height is not None and width is None:
                width = height * iw / ih
            elif width is not None and height is None:
                height = width * ih / iw
            elif width is not None and height is not None:
                # Fit inside the box, keeping its shape.
                scale = min(width / iw, height / ih)
                width, height = iw * scale, ih * scale

            if centre:
                x = x - width / 2

            canvas.drawImage(
                reader, x, top - height, width=width, height=height, mask="auto",
            )
            return height
        except Exception:  # pragma: no cover - a bad image is not fatal
            return 0

    @staticmethod
    def _cover_page(company, canvas, doc):
        """The photograph, the white mark on it, and PROPOSAL under it."""

        canvas.saveState()

        photo = _image_path(
            company["cover_image"] or settings.COMPANY_COVER_IMAGE, DEFAULT_COVER
        )
        drawn = QuotationPDFService._draw_image(canvas, photo, 0, PAGE_H, width=PAGE_W)

        if not drawn:
            # No photograph at all: a navy field in its place, so the white
            # mark still has something to sit on.
            drawn = 118 * mm
            canvas.setFillColor(NAVY)
            canvas.rect(0, PAGE_H - drawn, PAGE_W, drawn, stroke=0, fill=1)

        # Fade the foot of the picture into the page, as the printed cover
        # does - an uploaded photograph with a hard bottom edge would
        # otherwise end in a line across the page.
        fade = drawn * 0.28
        steps = 40
        canvas.setFillColor(colors.white)
        for step in range(steps):
            canvas.setFillAlpha((step + 1) / steps)
            band_top = PAGE_H - drawn + fade - fade * step / steps
            canvas.rect(0, band_top - fade / steps, PAGE_W, fade / steps + 0.5, stroke=0, fill=1)
        canvas.setFillAlpha(1)

        # Anything below the picture is plain page.
        canvas.rect(0, 0, PAGE_W, PAGE_H - drawn, stroke=0, fill=1)

        QuotationPDFService._draw_image(
            canvas,
            _image_path(company["cover_logo"], DEFAULT_COVER_LOGO),
            PAGE_W / 2, PAGE_H - 16 * mm, width=78 * mm, height=16 * mm, centre=True,
        )

        word = "PROPOSAL"
        size = 46
        canvas.setFillColor(colors.HexColor("#111111"))
        canvas.setFont("Helvetica-Bold", size)
        canvas.drawCentredString(PAGE_W / 2, PAGE_H - 161 * mm, word)

        canvas.restoreState()

    @staticmethod
    def _inner_page(company, canvas, doc):
        """The tint, the logo and company line over the rule, the contacts."""

        canvas.saveState()

        canvas.setFillColor(TINT)
        canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

        # The logo hangs from 12mm, clear of the non-printable edge most
        # office printers leave.
        QuotationPDFService._draw_image(
            canvas, _logo_path(company["logo_path"]),
            MARGIN, PAGE_H - 10 * mm, width=48 * mm, height=17 * mm,
            clear_white=True,
        )

        right = PAGE_W - MARGIN
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 9.6)
        canvas.drawRightString(right, PAGE_H - 16.5 * mm, (company["name"] or "").upper())

        if company["services_line"]:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 7.3)
            canvas.drawRightString(
                right, PAGE_H - 20.5 * mm,
                "  |  ".join(_split(company["services_line"])).upper(),
            )

        canvas.setStrokeColor(MAROON)
        canvas.setLineWidth(1.2)
        canvas.line(MARGIN, PAGE_H - 26 * mm, right, PAGE_H - 26 * mm)

        address = ", ".join(company["address"])
        first = "  |  ".join(
            part for part in (address, company["gstin"] and f"GSTIN: {company['gstin']}") if part
        )

        if first:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 7.3)
            canvas.drawCentredString(PAGE_W / 2, 15.5 * mm, first)

        contacts = "  |  ".join(
            part for part in (company["phone"], company["email"], company["website"]) if part
        )

        if contacts:
            canvas.setFillColor(NAVY)
            canvas.setFont("Helvetica-Bold", 10.5)
            canvas.drawCentredString(PAGE_W / 2, 9.5 * mm, contacts)

        canvas.restoreState()

    # ------------------------------------------------------------------
    # Cover
    # ------------------------------------------------------------------
    @staticmethod
    def _bill_to(quotation) -> list[tuple[str, str]]:
        """Who it is billed to, as (style, text) lines."""

        organisation = quotation.organization_name or quotation.contact_name or "-"
        lines = [("party_name", organisation)]

        if quotation.organization_name and quotation.contact_name:
            lines.append(("party_strong", f"Attn: {quotation.contact_name}"))

        address = quotation.billing_address if isinstance(quotation.billing_address, dict) else {}
        street = str(address.get("street") or "").strip()
        city = str(address.get("city") or "").strip()
        region = " ".join(
            str(part).strip()
            for part in (address.get("state"), address.get("zipCode") or address.get("zip_code"))
            if str(part or "").strip()
        )
        country = str(address.get("country") or "").strip()

        if street:
            lines.append(("party_line", street))

        rest = ", ".join(part for part in (city, region, country) if part)
        if rest:
            lines.append(("party_line", rest))

        return lines

    @staticmethod
    def _prepared_by(company, sender) -> list[tuple[str, str]]:
        """Whoever is sending it, over the company."""

        if sender:
            name, title = sender["name"], sender["title"]
            contact = "  |  ".join(part for part in (sender["phone"], sender["email"]) if part)
        else:
            name = company["signatory"] or company["name"]
            title = company["signatory_title"] if company["signatory"] else None
            contact = "  |  ".join(part for part in (company["phone"], company["email"]) if part)

        lines = [("party_name", name)]
        seen = {str(name or "").lower()}

        for style_name, text in (
            ("party_line", title),
            ("party_strong", (company["name"] or "").upper()),
            ("party_line", contact),
        ):
            if text and text.lower() not in seen:
                seen.add(text.lower())
                lines.append((style_name, text))

        return lines

    @staticmethod
    def _cover(quotation, company, sender, s) -> list:
        paragraphs = company["about_paragraphs"] or [ABOUT_FALLBACK]

        flow = [
            Paragraph(f"About {company['brand_name']}", s["about_title"]),
            *[Paragraph(p, s["about"]) for p in paragraphs],
        ]

        if company["tagline"]:
            flow += [Spacer(1, 5 * mm), Paragraph(company["tagline"], s["tagline"])]

        details = Table(
            [
                [Paragraph("DOCUMENT DETAILS", s["details_title"]), ""],
                [Paragraph("Proposal No.", s["details_label"]),
                 Paragraph(str(quotation.quote_number or quotation.id), s["details_value"])],
                [Paragraph("Issue Date", s["details_label"]),
                 Paragraph(_date(quotation.quotation_date), s["details_value"])],
                [Paragraph("Valid Until", s["details_label"]),
                 Paragraph(_date(quotation.validation_date), s["details_value"])],
            ],
            colWidths=[32 * mm, 38 * mm],
        )
        details.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, -1), BOX),
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
            ("LEFTPADDING", (1, 0), (1, -1), 1.5 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, 0), 5 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 5 * mm),
        ]))

        inner_w = PAGE_W - 2 * COVER_MARGIN - 6 * mm
        row = Table(
            [[Paragraph("For order confirmation and advance payment processing", s["purpose"]), details]],
            colWidths=[inner_w - 70 * mm, 70 * mm],
        )
        row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (0, 0), "BOTTOM"),
            ("VALIGN", (1, 0), (1, 0), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 1.5 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

        def party(label, lines):
            return [Paragraph(label, s["party_label"])] + [
                Paragraph(text, s[style_name]) for style_name, text in lines
            ]

        parties = Table(
            [[
                party("BILL TO", QuotationPDFService._bill_to(quotation)),
                party("PREPARED BY", QuotationPDFService._prepared_by(company, sender)),
            ]],
            colWidths=[inner_w / 2] * 2,
        )
        parties.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("LINEBEFORE", (1, 0), (1, 0), 0.6, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 4.5 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ]))

        # The cover is one page whatever the About text runs to: a longer
        # text is set a little smaller rather than pushing the addresses
        # onto the next page.
        return [
            NextPageTemplate("inner"),
            KeepInFrame(
                PAGE_W - 2 * COVER_MARGIN,
                PAGE_H - COVER_TEXT_TOP - 14 * mm,
                [*flow, Spacer(1, 8 * mm), row, Spacer(1, 2.5 * mm), parties],
                mode="shrink",
            ),
            PageBreak(),
        ]

    # ------------------------------------------------------------------
    # The offer
    # ------------------------------------------------------------------
    @staticmethod
    def _offer(quotation, s) -> list:
        header = [
            Paragraph(text, s["head"])
            # The currency is said once, in the heading, so the larger
            # amounts stay on one line.
            for text in ("#", "PRODUCT / MODEL", "HSN", "QTY", "UNIT PRICE<br/>(INR)",
                         "GST", "LINE AMOUNT<br/>(INR)")
        ]

        rows = [header]
        gst_percent = float(quotation.gst_percent or 0)

        for index, raw in enumerate(quotation.items or [], start=1):
            item = raw if isinstance(raw, dict) else {}

            quantity = float(item.get("quantity") or 1)
            unit_price = float(item.get("unit_price") or 0)
            discount = float(item.get("discount") or 0)
            tax = float(item.get("tax") if item.get("tax") is not None else gst_percent)

            # Before tax, as the printed summary has it: GST is its own
            # column here and its own line in the totals.
            net = quantity * unit_price * (1 - discount / 100)

            product = str(item.get("product") or "").strip()
            model = str(item.get("model") or "").strip()
            code = str(item.get("sku") or item.get("description") or "").strip()

            # The category over the model reads "Interactive Flat Panel /
            # 65" Interactive Flat Panel SPX" - the same words twice. The
            # model alone says it when it already carries the category.
            if model and product and product.lower() in model.lower():
                described = [model]
            else:
                described = [product or model or "-"]
                if model and model.lower() != (product or "").lower():
                    described.append(model)
            if code:
                described.append(f"Model: {code}")

            rows.append([
                Paragraph(str(index), s["cell"]),
                Paragraph("<br/>".join(described), s["cell_product"]),
                Paragraph(str(item.get("hsn") or "-"), s["cell"]),
                Paragraph(f"{quantity:g}", s["cell"]),
                Paragraph(_money(unit_price), s["cell_money"]),
                Paragraph(f"{tax:g}%", s["cell"]),
                Paragraph(_money(net), s["cell_money"]),
            ])

        if len(rows) == 1:
            rows.append(
                [Paragraph("", s["cell"]), Paragraph("No items on this proposal.", s["cell_product"])]
                + [Paragraph("", s["cell"])] * 5
            )

        widths = (25, 191, 56, 30, 68, 33, 118)
        scale = CONTENT_W / sum(widths)

        table = Table(rows, colWidths=[w * scale for w in widths], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, RULE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 0), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4.5),
            ("TOPPADDING", (0, 1), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
        ]))

        return [Spacer(1, 4 * mm), Paragraph("SUPPLY SUMMARY", s["section"]), table]

    @staticmethod
    def _totals(quotation, s) -> list:
        gst = float(quotation.gst_percent or 0)
        lines = []

        # Discount and ORC are internal margin, never shown to a client.
        # Delivery and installation are only listed when there are any.
        if quotation.freight_charges or quotation.installation_lumpsum:
            lines.append(("Subtotal", quotation.subtotal))
            if quotation.freight_charges:
                lines.append(("Delivery", quotation.freight_charges))
            if quotation.installation_lumpsum:
                lines.append(("Installation", quotation.installation_lumpsum))

        lines.append(("Taxable Subtotal", quotation.taxable_amount))
        lines.append((f"GST @ {gst:g}%", quotation.gst_amount))

        rows = [
            [Paragraph(label, s["total_label"]), Paragraph(f"INR {_money(value)}", s["total_value"])]
            for label, value in lines
        ]

        grand = len(rows)
        rows.append([
            Paragraph("GRAND TOTAL", s["grand_label"]),
            Paragraph(f"INR {_money(quotation.total_payable)}", s["grand_value"]),
        ])

        advance = float(quotation.advance_percent or 0)
        total = float(quotation.total_payable or 0)
        advance_amount = float(quotation.advance_amount or (total * advance / 100))

        rows.append([
            Paragraph(f"{advance:g}% Advance Due with Purchase Order", s["advance_label"]),
            Paragraph(f"INR {_money(advance_amount)}", s["advance_value"]),
        ])

        # The same figure in words, built from the same number. A customer
        # document carries both because digits can be altered with a pen
        # and words cannot.
        rows.append([
            Paragraph(
                f'<font color="#a3324c" size="7.4">AMOUNT IN WORDS</font>&nbsp;&nbsp;'
                f"{_words(quotation.total_payable)}",
                s["words"],
            ),
            "",
        ])

        table = Table(rows, colWidths=[CONTENT_W * 0.69, CONTENT_W * 0.31])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("GRID", (0, 0), (-1, -1), 0.5, RULE),
            ("BACKGROUND", (0, grand), (-1, grand), NAVY),
            ("BACKGROUND", (0, grand + 1), (-1, grand + 1), BOX),
            ("BACKGROUND", (0, -1), (-1, -1), colors.white),
            ("SPAN", (0, -1), (-1, -1)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, -1), (-1, -1), 6),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ]))

        return [Spacer(1, 6 * mm), table]

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
        advance = float(quotation.advance_percent or 0)
        total = float(quotation.total_payable or 0)
        advance_amount = float(quotation.advance_amount or (total * advance / 100))
        balance = quotation.on_delivery_amount or (total - advance_amount)

        schedule = [
            Paragraph("PAYMENT SCHEDULE", s["panel_label"]),
            Paragraph(
                f"{advance:g}% with Purchase Order&nbsp; - &nbsp;INR {_money(advance_amount)}",
                s["panel_strong"],
            ),
            Paragraph(
                f"{100 - advance:g}% against delivery&nbsp; - &nbsp;INR {_money(balance)}",
                s["panel_line"],
            ),
        ]

        # The validity period belongs on the document itself, not only in
        # the covering email - it is the one term a client checks first.
        points = [
            f"Offer valid through {_date(quotation.validation_date)}; reconfirm thereafter.",
            f"Taxes shown at {float(quotation.gst_percent or 0):g}%; final tax will be "
            "charged at the rate applicable on the tax invoice date.",
        ]

        for entry in quotation.terms or []:
            if isinstance(entry, dict) and entry.get("checked") and entry.get("label"):
                points.append(str(entry["label"]))

        conditions = [Paragraph("TERMS &amp; CONDITIONS", s["panel_label"])] + [
            Paragraph(f"&bull;&nbsp; {point}", s["term"]) for point in points
        ]

        panel = Table([[schedule, conditions]], colWidths=[CONTENT_W * 0.5] * 2)
        panel.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("LINEBEFORE", (1, 0), (1, 0), 0.6, RULE),
            ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
            ("VALIGN", (1, 0), (1, 0), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ]))

        flow = [
            Spacer(1, 6 * mm),
            Paragraph("PAYMENT &amp; COMMERCIAL TERMS", s["section"]),
            panel,
        ]

        if quotation.remarks:
            remarks = Table(
                [[[Paragraph("REMARKS", s["panel_label"]),
                   Paragraph(str(quotation.remarks), s["panel_line"])]]],
                colWidths=[CONTENT_W],
            )
            remarks.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]))
            flow += [Spacer(1, 3 * mm), remarks]

        return flow

    @staticmethod
    def _signature(company, sender, s) -> list:
        """Who issued it, and the space for the authorised signatory."""

        name = (sender and sender["name"]) or company["signatory"] or company["name"]
        title = (sender and sender["title"]) or company["signatory_title"]

        issued = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(_without_repeats([name, title]))

        return [
            Spacer(1, 7 * mm),
            KeepTogether([
                Paragraph(f"Issued by:&nbsp;&nbsp;{issued}", s["issued"]),
                Spacer(1, 7 * mm),
                Paragraph("Authorised Signatory&nbsp;&nbsp;&nbsp;", s["signatory"]),
            ]),
        ]

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
            topMargin=HEADER_H,
            bottomMargin=FOOTER_H,
            title=f"Proposal {quotation.quote_number or quotation.id}",
            author=company["name"],
        )

        cover_frame = Frame(
            COVER_MARGIN, 14 * mm, PAGE_W - 2 * COVER_MARGIN, PAGE_H - COVER_TEXT_TOP - 14 * mm,
            id="cover", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        inner_frame = Frame(
            MARGIN, FOOTER_H, CONTENT_W, PAGE_H - HEADER_H - FOOTER_H, id="inner",
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
            *QuotationPDFService._offer(quotation, s),
            *QuotationPDFService._totals(quotation, s),
            *QuotationPDFService._terms(quotation, s),
            *QuotationPDFService._signature(company, sender, s),
        ]

        document.build(story)

        return buffer.getvalue()
