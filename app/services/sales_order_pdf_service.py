"""The sales order as a PDF, for the people who decide on it and act on it.

Sent to each approver with the approval email and to Accounts with the
email that tells them the order is confirmed, and downloadable from the
order's page by the same people. It carries the whole order - who it is
for, where it goes, every line, every charge and the terms - so it can be
read, forwarded or filed without opening the CRM.

An internal document. Unlike the proforma invoice it states the discount
and the ORC, because those are exactly what the approvers are approving.
It carries no Approve or Reject: a file travels further than the inbox it
was sent to, and a decision is made in the email or in the CRM.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.timezone import now_ist, to_ist
from app.services.email_templates import _money

#: The roles that may download a sales order, besides a super admin. The
#: AVP is only the raiser's own - an AVP from another region has no
#: business with the order - while the CEO, the Founder and Accounts each
#: see every order there is.
EVERY_ORDER_ROLES = {"CEO", "Founder", "Accounts"}
LINE_ROLE = "AVP"


def can_download(order, current_user: dict, db) -> bool:
    """Whether this person may download the sales order PDF."""

    if (current_user or {}).get("is_super_admin"):
        return True

    from app.models.user import User
    from app.services.approval_service import ApprovalService, _role_names

    user_id = (current_user or {}).get("user_id")
    user = db.query(User).filter(User.id == user_id).first() if user_id else None

    if user is None:
        return False

    roles = _role_names(user)

    if roles & EVERY_ORDER_ROLES:
        return True

    if LINE_ROLE in roles and order.creator_id:
        line = ApprovalService._managers_of(str(order.creator_id), db)
        return any(str(manager.id) == str(user.id) for manager in line)

    return False


#: The order page's own palette, so the PDF reads as the same screen.
SLATE_900 = "#141414"
SLATE_700 = "#334155"
SLATE_500 = "#64748b"
SLATE_400 = "#94a3b8"
SLATE_200 = "#e2e8f0"
SLATE_50 = "#f8fafc"
NAVY_DARK = "#233353"
ROSE = "#e11d48"
GREEN = "#059669"
GREEN_WASH = "#ecfdf5"


def _style(name, size, color=SLATE_900, bold=False, align=None, leading=None):
    style = ParagraphStyle(
        name,
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading or size * 1.35,
        textColor=colors.HexColor(color),
    )
    if align == "right":
        style.alignment = TA_RIGHT
    return style


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "")).replace("\n", "<br/>"), style)


def _date(value) -> str:
    if not value:
        return "-"
    try:
        return to_ist(value).strftime("%d/%m/%Y")
    except Exception:  # noqa: BLE001 - a date we cannot read is shown as absent
        return "-"


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rs(value) -> str:
    """Rs.82,600 - the page's figures. The PDF fonts have no rupee sign."""

    amount = _number(value)
    text = _money(abs(amount))
    if text.endswith(".00"):
        text = text[:-3]
    return f"{'-' if amount < 0 else ''}Rs.{text}"


class SalesOrderPDFService:
    """The order laid out as the New Sales Order page is - its sections,
    its labels and its totals - read-only, headed Sales Order, and without
    the page's Cancel, Save as Draft and Send For Approval or any of its
    editing controls."""

    @staticmethod
    def filename(order) -> str:
        reference = order.order_number or f"SO-{order.id}"
        return f"Sales_Order_{reference}.pdf"

    @staticmethod
    def render(order, db=None) -> bytes:
        reference = order.order_number or f"SO-{order.id}"

        label = _style("label", 8.5, SLATE_500)
        value = _style("value", 9, SLATE_900)
        bold = _style("bold", 9, SLATE_900, bold=True)
        small = _style("small", 7.5, SLATE_400)
        sub = _style("sub", 9, NAVY_DARK, bold=True)
        section = _style("section", 11.5, SLATE_700)
        body = _style("body", 9, SLATE_700, leading=13)

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
            title=f"Sales Order {reference}",
        )
        width = A4[0] - 24 * mm
        story: list = []

        # The page's sections run one after another down its main panel,
        # so here they flow and break across pages where they must.
        # The page frame keeps 6pt each side for itself.
        inner = width - 12

        def heading(text):
            line = Table([[_p(text, section)]], colWidths=[inner], hAlign="LEFT")
            line.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.HexColor(SLATE_200)),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            return [line, Spacer(1, 3 * mm)]

        def pairs(rows, label_width=30 * mm):
            """Label: value, two to a row, as the page lays its fields."""
            col = inner / 2
            data = []
            for i in range(0, len(rows), 2):
                row = []
                for k, v in rows[i:i + 2]:
                    row += [_p(k, label), _p(v if v not in (None, "") else "-", value)]
                while len(row) < 4:
                    row.append("")
                data.append(row)
            table = Table(data, colWidths=[label_width, col - label_width, label_width, col - label_width], hAlign="LEFT")
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]))
            return table

        # The page header: the title, the stage and where it sits, and the
        # Order Summary card the page keeps beside the form.
        status = str(order.status or "").replace("_", " ").title() or "-"
        title_block = [
            # Built as markup: the stage sits beside the title like the
            # page's Draft badge.
            Paragraph(
                f"Sales Order <font size=9 color='{NAVY_DARK}'>&nbsp;&nbsp;{escape(status)}</font>",
                _style("title", 18, SLATE_900),
            ),
            Spacer(1, 1.5 * mm),
            _p(f"Sales Order  \u203a  {reference}", _style("crumb", 8.5, SLATE_500)),
        ]

        summary = Table(
            [[_p("Order Summary", _style("os", 10, SLATE_700))],
             [Table([[_p("Total Payable", _style("tp", 9, SLATE_900, bold=True)),
                      _p(_rs(order.grand_total), _style("tpv", 13, SLATE_900, bold=True, align="right"))]],
                    colWidths=[30 * mm, 34 * mm],
                    style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(GREEN_WASH)),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)])]],
            colWidths=[66 * mm],
        )
        summary.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(SLATE_200)),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))

        # Attached Documents, under the Order Summary as the page has it.
        files = [
            (f.get("name") if isinstance(f, dict) else str(f))
            for f in (order.attachments or [])
        ]
        attached = Table(
            [[_p("Attached Documents", _style("ad", 10, SLATE_700))]]
            + ([[_p(f"•  {name}", _style("af", 8, SLATE_700))] for name in files if name]
               or [[_p("No documents attached.", small)]]),
            colWidths=[66 * mm],
        )
        attached.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(SLATE_200)),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))

        story.append(Table(
            [[title_block, [summary, Spacer(1, 3 * mm), attached]]], colWidths=[inner - 70 * mm, 70 * mm], hAlign="LEFT",
            style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                   ("ALIGN", (1, 0), (1, 0), "RIGHT")],
        ))
        story.append(Spacer(1, 5 * mm))

        info = order.customer_information or {}
        contact = info.get("primary_contact") or {}

        # Order Information, Organization Details and the Primary Contact.
        order_section = heading("Order Information") + [
            pairs([
                ("Sales Order ID:", f"#{reference}"),
                ("Assigned To:", order.assigned_to or order.sales_executive or "Unassigned"),
                ("Proposal ID:", order.quotation_id),
                ("Order Date:", _date(order.order_date or order.created_at)),
                ("PO Number", order.po_number),
                ("PO Date", _date(order.po_date) if order.po_date else "-"),
            ]),
            Spacer(1, 5 * mm),
            *heading("Organization Details"),
            pairs([
                ("Registration:", info.get("registration")),
                ("Customer Type:", order.customer_type or info.get("customer_type")),
                ("Organization Name:", order.company_name or info.get("organization_name")),
                ("GST:", info.get("gst")),
                ("PAN:", info.get("pan")),
                ("COI Number:", info.get("cin") or info.get("coi_number")),
            ]),
            Spacer(1, 3 * mm),
            _p("Primary Contact", sub),
            Spacer(1, 1.5 * mm),
            pairs([
                ("Customer:", contact.get("name") or order.customer_name),
                ("Designation:", contact.get("designation")),
                ("Phone:", contact.get("phone")),
                ("Email:", contact.get("email")),
            ]),
            Spacer(1, 5 * mm),
            *heading("Location Information"),
        ]

        billing = order.billing_address or {}
        shipping = order.shipping_address or {}
        same = billing and billing == shipping

        def address(a):
            rows = [
                ("Street Address", a.get("street")),
                ("Country", a.get("country")),
                ("State / Province", a.get("state")),
                ("City", a.get("city")),
                ("PIN / ZIP Code", a.get("pin") or a.get("zipCode")),
            ]
            return Table([[_p(k, label), _p(v or "-", value)] for k, v in rows],
                         colWidths=[30 * mm, inner / 2 - 34 * mm],
                         style=[("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)])

        order_section.append(Table(
            [[_p("Billing Address", sub),
              _p("Shipping Address" + ("  (Same as Billing)" if same else ""), sub)],
             [address(billing), address(shipping)]],
            colWidths=[inner / 2] * 2, hAlign="LEFT",
            style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)],
        ))
        story += order_section
        story.append(Spacer(1, 6 * mm))

        # Products & Order Items, with the page's columns.
        th = _style("th", 7.5, SLATE_500)
        th_r = _style("thr", 7.5, SLATE_500, align="right")
        td = _style("td", 8, SLATE_900)
        td_r = _style("tdr", 8, SLATE_900, align="right")
        rows = [[_p(h, th if i < 7 else th_r) for i, h in enumerate(
            ("Product", "Model / Variant", "HSN / SAC", "Qty", "Discount", "Tax", "Warranty", "Unit Price", "Selling Price"))]]

        for item in order.items or []:
            qty = _number(item.get("qty") or item.get("quantity_case"))
            rate = _number(item.get("rate") or item.get("price"))
            discount = _number(item.get("discount"))
            product = [Paragraph(f"<b>{escape(str(item.get('product') or item.get('item') or '-'))}</b>", td)]
            if item.get("sku"):
                product.append(_p(f"SKU: {item['sku']}", small))
            rows.append([
                product,
                _p(item.get("model") or item.get("description") or "-", td),
                _p(item.get("hsn") or "-", td),
                _p(f"{qty:g}", td),
                _p(f"{discount:g} %", td),
                _p(f"{_number(item.get('tax_rate')):g} %", td),
                _p(item.get("warranty_term") or "-", td),
                _p(_rs(rate), td_r),
                _p(_rs(rate * (1 - discount / 100)), td_r),
            ])

        if len(rows) == 1:
            rows.append([_p("No products added.", td)] + [""] * 8)

        fixed = [32 * mm, 34 * mm, 18 * mm, 10 * mm, 16 * mm, 12 * mm, 18 * mm, 0, 23 * mm]
        fixed[7] = inner - sum(fixed)
        items = Table(rows, colWidths=fixed, repeatRows=1, hAlign="LEFT")
        items.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(SLATE_200)),
            ("LINEBELOW", (0, 0), (-1, -2), 0.6, colors.HexColor(SLATE_200)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))

        # The totals column, line for line as the page lists it.
        orc_pct = _number(order.orc_percent)
        gst_pct = _number(order.gst_percent if order.gst_percent is not None else 18)
        grand = _number(order.grand_total)
        paid = _number(order.advance_received)
        lk = _style("lk", 9, SLATE_500)
        lv = _style("lv", 9, SLATE_900, align="right")
        note = _style("note", 7, SLATE_400, align="right")

        def line(k, v, tone=SLATE_900, strong=False):
            return [_p(k, _style("k", 9.5 if strong else 9, SLATE_900 if strong else SLATE_500, bold=strong)),
                    _p(v, _style("v", 10 if strong else 9, tone, bold=strong, align="right"))]

        margin_note = "Recorded against margin. Does not change the order value."
        totals = [
            line("Subtotal:", _rs(order.total_amount)),
            line("Total Discount", f"-{_rs(order.discount_amount)}", ROSE),
            line(f"ORC ({orc_pct:.2f}%)", _rs(order.orc_amount)),
            ["", _p(margin_note, note)],
            line("Freight Charges", f"+{_rs(order.freight_charges)}"),
            line("Installation", f"+{_rs(order.installation_lumpsum)}"),
            line("Shifting (our cost)", _rs(order.shifting_charges)),
            ["", _p(margin_note, note)],
            line("Taxable Amount:", _rs(order.taxable_amount)),
            line(f"Estimated GST ({gst_pct:g}%)", _rs(order.gst_amount)),
            line("Sales Order Value:", _rs(grand), strong=True),
        ]
        totals_table = Table(totals, colWidths=[52 * mm, 38 * mm], hAlign="RIGHT")
        totals_table.setStyle(TableStyle([
            ("LINEABOVE", (0, 8), (-1, 8), 0.6, colors.HexColor(SLATE_200)),
            ("LINEABOVE", (0, 10), (-1, 10), 0.6, colors.HexColor(SLATE_200)),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        revenue_rows = [
            [_p("REVENUE CALCULATION", _style("rc", 7.5, SLATE_400, bold=True)), ""],
            [_p("Sales Order Value", _style("r", 8.5, SLATE_700)), _p(_rs(grand), _style("rv", 8.5, SLATE_900, align="right"))],
        ]
        for k, v in (("Less Delivery", order.freight_charges), ("Less Installation", order.installation_lumpsum),
                     ("Less Shifting", order.shifting_charges), (f"Less GST ({gst_pct:g}%)", order.gst_amount),
                     ("Less ORC", order.orc_amount)):
            revenue_rows.append([_p(k, _style("r", 8.5, SLATE_700)),
                                 _p(f"-{_rs(v)}", _style("rr", 8.5, ROSE, align="right"))])
        revenue_rows.append([_p("Total Revenue:", _style("tr", 9.5, SLATE_900, bold=True)),
                             _p(_rs(order.total_revenue), _style("trv", 10.5, GREEN, bold=True, align="right"))])
        revenue = Table(revenue_rows, colWidths=[52 * mm, 38 * mm], hAlign="RIGHT")
        revenue.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(SLATE_50)),
            ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor(SLATE_200)),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        after = Table([line("Amount Paid", f"-{_rs(paid)}", ROSE), line("Balance Due:", _rs(grand - paid))],
                      colWidths=[52 * mm, 38 * mm], hAlign="RIGHT",
                      style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])

        money_column = [totals_table, Spacer(1, 3 * mm), revenue, Spacer(1, 3 * mm), after]
        # Each block is its own table, so a page can break between lines
        # instead of pushing the whole column on and leaving a gap.
        story += heading("Products & Order Items") + [
            items,
            Spacer(1, 4 * mm),
            *money_column,
            Spacer(1, 6 * mm),
        ]

        # Terms, Conditions & Technical Notes.
        def boxed(flowables):
            table = Table([[flowables]], colWidths=[inner], hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(SLATE_50)),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            return table

        terms = [str(t).strip() for t in (order.commercial_terms or []) if str(t).strip()]
        advance = _number(order.advance_percent if order.advance_percent is not None else 30)
        terms_section = heading("Terms, Conditions & Technical Notes") + [
            boxed([_p("Pre-filled Commercial Conditions", _style("pf", 8.5, SLATE_500, bold=True)), Spacer(1, 2 * mm)]
                  + ([Paragraph(f"•&nbsp;&nbsp;{escape(t)}", body) for t in terms]
                     or [_p("No conditions on this order.", small)])),
            Spacer(1, 3 * mm),
            _p("Payment Terms", _style("ptl", 8.5, SLATE_500)),
            Spacer(1, 1.5 * mm),
            boxed([_p(order.payment_terms or "-", body)]),
            Spacer(1, 1.5 * mm),
            Paragraph(
                "The advance written here is what the Proforma Invoice asks for and what the "
                f"summary splits on — currently <b>{advance:g}%</b>.", small,
            ),
            Spacer(1, 3 * mm),
            _p("Technical Scope & Deployment Notes", _style("tn", 8.5, SLATE_500)),
            Spacer(1, 1.5 * mm),
            boxed([_p(order.technical_notes or "-", body)]),
        ]
        story += terms_section

        story += [
            Spacer(1, 5 * mm),
            _p(f"Generated {now_ist().strftime('%d %b %Y, %H:%M IST')} from the CRM.", small),
        ]

        doc.build(story)
        return buffer.getvalue()


def sales_order_attachment(order_id: int, db) -> list[dict] | None:
    """The order as an email attachment, or None when there is no order."""

    from app.models.sales_order import SalesOrder

    order = db.query(SalesOrder).filter(SalesOrder.id == order_id).first()

    if order is None:
        return None

    return [{
        "filename": SalesOrderPDFService.filename(order),
        "content": SalesOrderPDFService.render(order, db),
        "mime_type": "application/pdf",
    }]
