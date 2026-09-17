from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.workflow_status import (
    PROFORMA_INVOICE_TRANSITIONS,
    ProformaInvoiceStatus,
    SalesOrderStatus,
    assert_transition,
)
from app.models.proforma_invoice import ProformaInvoice
from app.models.proforma_invoice_activity import ProformaInvoiceActivity
from app.models.sales_order import SalesOrder
from app.services.email_service import EmailService
from app.services.lead_service import get_visible_creator_user_ids
from app.services.quotation_service import format_inr, sanitize_email_html
from app.services.sales_order_service import (
    SalesOrderService,
    _items_to_json,
    compute_order_totals,
)

#: Headline written onto the activity entry when an invoice reaches a status.
PROFORMA_INVOICE_STATUS_ACTIONS: dict[str, str] = {
    ProformaInvoiceStatus.DRAFT: "Proforma Invoice Drafted",
    ProformaInvoiceStatus.GENERATED: "Proforma Invoice Generated",
    ProformaInvoiceStatus.SENT: "Proforma Invoice Sent",
    ProformaInvoiceStatus.CANCELLED: "Proforma Invoice Cancelled",
}

#: A sales order can be invoiced once it is confirmed. A draft has not been
#: agreed yet, and a cancelled order has nothing left to bill.
INVOICEABLE_ORDER_STATUSES = {
    SalesOrderStatus.CONFIRMED,
    SalesOrderStatus.RELEASED,
    SalesOrderStatus.ON_HOLD,
    SalesOrderStatus.COMPLETED,
}

#: Days between issue and due date when the form does not say otherwise.
DEFAULT_VALIDITY_DAYS = 30

#: What may still change at each status. A draft is fully editable. Once
#: generated the lines, dates and addresses are fixed but the charges can
#: still be corrected before it goes out; once sent only the payment
#: received against it moves.
EDITABLE_FIELDS: dict[str, set[str]] = {
    ProformaInvoiceStatus.DRAFT: {
        "issue_date", "due_date", "assigned_to", "billing_address",
        "shipping_address", "items", "freight_charges",
        "installation_lumpsum", "gst_percent", "amount_paid",
        "advance_percent", "commercial_terms", "technical_notes",
        "attachments",
    },
    ProformaInvoiceStatus.GENERATED: {
        "freight_charges", "installation_lumpsum", "gst_percent",
        "amount_paid",
    },
    ProformaInvoiceStatus.SENT: {"amount_paid"},
    ProformaInvoiceStatus.CANCELLED: set(),
}

FIELD_LABELS = {
    "issue_date": "PI Date (Issue)",
    "due_date": "PI Date (Due)",
    "assigned_to": "Assigned To",
    "billing_address": "Billing Address",
    "shipping_address": "Shipping Address",
    "items": "Products",
    "freight_charges": "Freight Charges",
    "installation_lumpsum": "Lumpsum (Installation)",
    "gst_percent": "Estimated GST",
    "amount_paid": "Amount Paid",
    "advance_percent": "Payment Terms",
    "commercial_terms": "Commercial Conditions",
    "technical_notes": "Technical Notes",
    "attachments": "Attached Documents",
}

MONEY_FIELDS = (
    "items", "freight_charges", "installation_lumpsum", "gst_percent",
    "amount_paid",
)


def _to_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _pick(value, fallback):
    return fallback if value is None else value


def _totals(invoice: ProformaInvoice, **overrides) -> dict:
    """Every money figure on the invoice, derived from its lines and charges.

    Uses the sales order's own arithmetic - including any discount and ORC
    carried over from the order - so an invoice raised for a whole order
    totals exactly what the order does.
    """

    totals = compute_order_totals(
        overrides.get("items", invoice.items),
        discount_mode=invoice.discount_mode,
        discount_input=invoice.discount_input,
        orc_mode=invoice.orc_mode,
        orc_input=invoice.orc_input,
        freight_charges=overrides.get("freight_charges", invoice.freight_charges) or 0.0,
        installation_lumpsum=(
            overrides.get("installation_lumpsum", invoice.installation_lumpsum) or 0.0
        ),
        gst_percent=_pick(
            overrides.get("gst_percent", invoice.gst_percent),
            18.0,
        ),
        advance_received=overrides.get("amount_paid", invoice.amount_paid) or 0.0,
    )

    totals["amount_paid"] = totals.pop("advance_received")
    totals["balance_due"] = totals.pop("outstanding_balance")

    # The invoice keeps the order's discount and ORC settings as they were.
    for key in ("discount_mode", "discount_input", "orc_mode", "orc_input"):
        totals.pop(key, None)

    return totals


def _gst_clause(invoice: ProformaInvoice) -> str:
    percent = float(invoice.gst_percent or 0)

    if percent <= 0:
        return " (GST not applicable)"

    return (
        f" (inclusive of {format_inr(invoice.gst_amount)} GST at "
        f"{percent:g}%)"
    )


class ProformaInvoiceService:

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    def get_visible(
        current_user: dict,
        db: Session,
        sales_order_id: int | None = None,
    ) -> list[ProformaInvoice]:
        query = db.query(ProformaInvoice)

        visible_ids = get_visible_creator_user_ids(current_user, db)

        # An empty list means unrestricted access, as for sales orders.
        if visible_ids:
            query = query.filter(
                or_(
                    ProformaInvoice.creator_id.in_(
                        [UUID(uid) for uid in visible_ids]
                    ),
                    ProformaInvoice.creator_id.is_(None),
                )
            )

        if sales_order_id is not None:
            query = query.filter(ProformaInvoice.sales_order_id == sales_order_id)

        return query.order_by(ProformaInvoice.id.desc()).all()

    @staticmethod
    def get_by_id(invoice_id: int, db: Session) -> ProformaInvoice:
        invoice = (
            db.query(ProformaInvoice)
            .filter(ProformaInvoice.id == invoice_id)
            .first()
        )

        if invoice is None:
            raise HTTPException(status_code=404, detail="Proforma invoice not found")

        return invoice

    @staticmethod
    def company_profile() -> dict:
        """Seller identity and banking details printed on every invoice."""

        address = [
            line.strip()
            for line in (settings.COMPANY_ADDRESS or "").split("|")
            if line.strip()
        ]

        return {
            "legal_name": settings.COMPANY_LEGAL_NAME or None,
            "address_lines": address,
            "gstin": settings.COMPANY_GSTIN or None,
            "bank": {
                "beneficiary_name": settings.BANK_BENEFICIARY_NAME or None,
                "bank_name": settings.BANK_NAME or None,
                "branch": settings.BANK_BRANCH or None,
                "account_number": settings.BANK_ACCOUNT_NUMBER or None,
                "ifsc": settings.BANK_IFSC or None,
                "upi_vpa": settings.BANK_UPI_VPA or None,
            },
            "signatory": {
                "name": settings.SIGNATORY_NAME or None,
                "title": settings.SIGNATORY_TITLE or None,
            },
            "configured": bool(
                settings.BANK_ACCOUNT_NUMBER and settings.BANK_IFSC
            ),
        }

    # ------------------------------------------------------------------
    # Authorisation
    # ------------------------------------------------------------------
    @staticmethod
    def assert_can_edit(
        invoice: ProformaInvoice,
        current_user: dict,
        db: Session,
    ) -> None:
        if current_user.get("is_super_admin", False):
            return

        if invoice.creator_id is None:
            return

        if str(invoice.creator_id) == current_user.get("user_id"):
            return

        visible_ids = get_visible_creator_user_ids(current_user, db)

        if visible_ids and str(invoice.creator_id) in visible_ids:
            return

        raise HTTPException(
            status_code=403,
            detail=(
                "Permission denied. You can only modify proforma invoices "
                "created by yourself or your subordinates."
            ),
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    @staticmethod
    def next_pi_number(db: Session) -> str:
        """The next PI-XXXXX reference.

        Follows the highest reference issued rather than the row id: ids
        skip whenever an insert is rolled back, and invoice numbers should
        run on without gaps.
        """

        highest = 0

        for (reference,) in db.query(ProformaInvoice.pi_number).all():
            digits = "".join(ch for ch in (reference or "") if ch.isdigit())
            if digits:
                highest = max(highest, int(digits))

        return f"PI-{highest + 1:05d}"

    @staticmethod
    def create(request, current_user: dict, db: Session) -> ProformaInvoice:
        order = SalesOrderService.get_by_id(request.sales_order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        if order.status not in INVOICEABLE_ORDER_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Sales order {order.order_number or order.id} is "
                    f"{order.status.replace('_', ' ').title()}. A proforma "
                    "invoice can only be raised against a confirmed order."
                ),
            )

        status = str(request.status or ProformaInvoiceStatus.DRAFT).upper()

        if status not in (
            ProformaInvoiceStatus.DRAFT,
            ProformaInvoiceStatus.GENERATED,
        ):
            raise HTTPException(
                status_code=400,
                detail="A new proforma invoice starts as DRAFT or GENERATED.",
            )

        issue_date = request.issue_date or datetime.utcnow()
        due_date = request.due_date or issue_date + timedelta(days=DEFAULT_VALIDITY_DAYS)

        ProformaInvoiceService._validate_dates(issue_date, due_date)

        items = (
            _items_to_json(request.items)
            if request.items is not None
            else list(order.items or [])
        )

        if status == ProformaInvoiceStatus.GENERATED and not items:
            raise HTTPException(
                status_code=400,
                detail="Add at least one product before generating the invoice.",
            )

        first_name = current_user.get("first_name", "")
        last_name = current_user.get("last_name", "")

        invoice = ProformaInvoice(
            pi_number=ProformaInvoiceService.next_pi_number(db),
            sales_order_id=order.id,
            status=status,
            issue_date=issue_date,
            due_date=due_date,
            assigned_to=_pick(request.assigned_to, order.assigned_to),
            customer_name=order.customer_name,
            company_name=order.company_name,
            customer_type=order.customer_type,
            state=order.state,
            customer_information=order.customer_information,
            billing_address=_pick(request.billing_address, order.billing_address),
            shipping_address=_pick(request.shipping_address, order.shipping_address),
            items=items,
            discount_mode=order.discount_mode,
            discount_input=order.discount_input,
            orc_mode=order.orc_mode,
            orc_input=order.orc_input,
            freight_charges=_pick(request.freight_charges, order.freight_charges or 0.0),
            installation_lumpsum=_pick(
                request.installation_lumpsum, order.installation_lumpsum or 0.0
            ),
            gst_percent=_pick(
                request.gst_percent,
                order.gst_percent if order.gst_percent is not None else 18.0,
            ),
            amount_paid=request.amount_paid or 0.0,
            advance_percent=_pick(
                request.advance_percent,
                order.advance_percent if order.advance_percent is not None else 30.0,
            ),
            commercial_terms=_pick(request.commercial_terms, order.commercial_terms),
            technical_notes=_pick(request.technical_notes, order.technical_notes),
            attachments=_pick(request.attachments, order.attachments),
            generated_at=(
                datetime.utcnow()
                if status == ProformaInvoiceStatus.GENERATED
                else None
            ),
            creator_id=_to_uuid(current_user.get("user_id")),
            creator_name=f"{first_name} {last_name}".strip() or "User",
        )

        for key, value in _totals(invoice).items():
            setattr(invoice, key, value)

        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        ProformaInvoiceService.record_activity(
            db,
            invoice,
            action=PROFORMA_INVOICE_STATUS_ACTIONS[status],
            description=(
                f"Proforma invoice {invoice.pi_number} raised against sales "
                f"order {order.order_number or order.id}."
            ),
            to_status=status,
            user_id=current_user.get("user_id"),
        )

        return invoice

    @staticmethod
    def _validate_dates(issue_date, due_date) -> None:
        if issue_date and due_date and due_date < issue_date:
            raise HTTPException(
                status_code=400,
                detail="PI Date (Due) cannot be before PI Date (Issue).",
            )

    @staticmethod
    def update(invoice_id: int, request, current_user: dict, db: Session) -> ProformaInvoice:
        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        changes = request.model_dump(exclude_unset=True)
        changes = {key: value for key, value in changes.items() if value is not None}

        allowed = EDITABLE_FIELDS.get(invoice.status, set())
        blocked = [key for key in changes if key not in allowed]

        if blocked:
            status = invoice.status.title()
            names = ", ".join(FIELD_LABELS.get(key, key) for key in blocked)

            raise HTTPException(
                status_code=400,
                detail=(
                    f"{names} cannot be changed on a {status} proforma "
                    "invoice."
                ),
            )

        ProformaInvoiceService._validate_dates(
            changes.get("issue_date", invoice.issue_date),
            changes.get("due_date", invoice.due_date),
        )

        previous_paid = invoice.amount_paid or 0.0

        for key, value in changes.items():
            if key == "items":
                invoice.items = _items_to_json(request.items)
            else:
                setattr(invoice, key, value)

        if any(key in changes for key in MONEY_FIELDS):
            for key, value in _totals(invoice).items():
                setattr(invoice, key, value)

        if "amount_paid" in changes and (invoice.amount_paid or 0.0) != previous_paid:
            ProformaInvoiceService.record_activity(
                db,
                invoice,
                action="Payment Recorded",
                description=(
                    f"Amount paid updated from {format_inr(previous_paid)} to "
                    f"{format_inr(invoice.amount_paid)}. Balance due "
                    f"{format_inr(invoice.balance_due)}."
                ),
                user_id=current_user.get("user_id"),
                commit=False,
            )

        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def _move(
        invoice: ProformaInvoice,
        target: str,
        current_user: dict,
        db: Session,
        description: str | None = None,
    ) -> ProformaInvoice:
        target = str(target or "").upper()

        if target not in ProformaInvoiceStatus.ALL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid proforma invoice status '{target}'. Expected one "
                    f"of: {', '.join(ProformaInvoiceStatus.ALL)}."
                ),
            )

        previous = invoice.status

        assert_transition(
            "proforma invoice",
            PROFORMA_INVOICE_TRANSITIONS,
            previous,
            target,
        )

        if target == ProformaInvoiceStatus.GENERATED and not invoice.items:
            raise HTTPException(
                status_code=400,
                detail="Add at least one product before generating the invoice.",
            )

        invoice.status = target

        if target == ProformaInvoiceStatus.GENERATED and not invoice.generated_at:
            invoice.generated_at = datetime.utcnow()

        if target != previous:
            ProformaInvoiceService.record_activity(
                db,
                invoice,
                action=PROFORMA_INVOICE_STATUS_ACTIONS.get(target, "Status Updated"),
                description=description,
                from_status=previous,
                to_status=target,
                user_id=current_user.get("user_id"),
                commit=False,
            )

        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def update_status(invoice_id: int, request, current_user: dict, db: Session) -> ProformaInvoice:
        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        return ProformaInvoiceService._move(
            invoice,
            request.status,
            current_user,
            db,
            description=getattr(request, "remarks", None),
        )

    @staticmethod
    def generate(invoice_id: int, current_user: dict, db: Session) -> ProformaInvoice:
        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        return ProformaInvoiceService._move(
            invoice,
            ProformaInvoiceStatus.GENERATED,
            current_user,
            db,
            description=f"Proforma invoice {invoice.pi_number} generated.",
        )

    @staticmethod
    def delete(invoice_id: int, current_user: dict, db: Session) -> None:
        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        if invoice.status != ProformaInvoiceStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Only a draft proforma invoice can be deleted. Cancel it "
                    "instead so the record of it is kept."
                ),
            )

        db.delete(invoice)
        db.commit()

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    @staticmethod
    def default_email_body(invoice: ProformaInvoice) -> str:
        info = invoice.customer_information or {}
        contact = (info.get("primary_contact") or {}).get("name") or invoice.customer_name

        lines = [
            f"Dear {contact or 'Sir/Madam'},",
            "",
            f"Please find attached the official Proforma Invoice "
            f"(#{invoice.pi_number}) for {invoice.company_name or invoice.customer_name}.",
            "",
            "Key Highlights:",
        ]

        for item in invoice.items or []:
            quantity = float(item.get("quantity_case") or item.get("qty") or 1)
            name = item.get("model") or item.get("description") or item.get("product") or "Item"
            lines.append(f"  - {quantity:g}x {name}")

        lines += [
            f"  - Total Value: {format_inr(invoice.grand_total)}{_gst_clause(invoice)}",
            "",
            "Kindly review the attached proforma invoice and let us know if "
            "you require any adjustments or technical clarifications.",
            "",
            "Warm regards,",
        ]

        return "\n".join(lines)

    @staticmethod
    def send(invoice_id: int, request, current_user: dict, db: Session) -> dict:
        """Email the invoice to the customer and move it to SENT."""

        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        if invoice.status == ProformaInvoiceStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail="Generate the proforma invoice before sending it to the customer.",
            )

        if invoice.status == ProformaInvoiceStatus.CANCELLED:
            raise HTTPException(
                status_code=400,
                detail="A cancelled proforma invoice cannot be sent.",
            )

        subject = request.subject or (
            f"Proforma Invoice #{invoice.pi_number} - "
            f"{invoice.company_name or invoice.customer_name} "
            "[Payment Mobilization Request]"
        )

        body = request.body or ProformaInvoiceService.default_email_body(invoice)

        if request.body_html and request.body_html.strip():
            inner = sanitize_email_html(request.body_html)
        else:
            inner = (
                body.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br/>")
            )

        html = (
            '<div style="font-family:Arial,Helvetica,sans-serif;'
            'font-size:14px;color:#1e293b;line-height:1.6">'
            f"{inner}</div>"
        )

        if request.test_only:
            address = current_user.get("email")

            if not address:
                raise HTTPException(
                    status_code=400,
                    detail="Your account has no email address to send a test to.",
                )

            delivered = EmailService.send(
                to=address,
                subject=f"[TEST] {subject}",
                text_body=body,
                html_body=html,
            )

            return {
                "delivered": delivered,
                "test_only": True,
                "recipients": [address],
                "invoice": invoice,
            }

        recipients = [a.strip() for a in (request.to or []) if a and a.strip()]

        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="At least one recipient is required to send this proforma invoice.",
            )

        cc = [a.strip() for a in (request.cc or []) if a and a.strip()]
        bcc = [a.strip() for a in (request.bcc or []) if a and a.strip()]

        delivered = EmailService.send(
            to=recipients,
            subject=subject,
            text_body=body,
            html_body=html,
            cc=cc,
            bcc=bcc,
        )

        if not delivered:
            raise HTTPException(
                status_code=502,
                detail=(
                    "The proforma invoice could not be emailed. Check the "
                    "SMTP settings in the backend environment and try again."
                ),
            )

        # Only recorded once the message actually went out.
        invoice.sent_at = datetime.utcnow()
        invoice.sent_to = ", ".join(recipients)[:500]
        invoice.sent_subject = subject[:500]
        invoice.send_options = {
            "track_opens": bool(request.track_opens),
            "alert_on_download": bool(request.alert_on_download),
            "attach_gst_audit_trail": bool(request.attach_gst_audit_trail),
            "notify_lead_owner": bool(request.notify_lead_owner),
        }

        previous = invoice.status
        invoice.status = ProformaInvoiceStatus.SENT

        ProformaInvoiceService.record_activity(
            db,
            invoice,
            action=(
                PROFORMA_INVOICE_STATUS_ACTIONS[ProformaInvoiceStatus.SENT]
                if previous != ProformaInvoiceStatus.SENT
                else "Proforma Invoice Resent"
            ),
            description="Emailed to " + ", ".join(recipients) + ".",
            from_status=previous,
            to_status=ProformaInvoiceStatus.SENT,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        return {
            "delivered": True,
            "test_only": False,
            "recipients": recipients,
            "invoice": invoice,
        }

    # ------------------------------------------------------------------
    # Activity History
    # ------------------------------------------------------------------
    @staticmethod
    def record_activity(
        db: Session,
        invoice: ProformaInvoice,
        action: str,
        description: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        user_id: str | None = None,
        commit: bool = True,
    ) -> ProformaInvoiceActivity:
        activity = ProformaInvoiceActivity(
            proforma_invoice_id=invoice.id,
            action=action,
            description=description or None,
            from_status=from_status,
            to_status=to_status,
            created_by=_to_uuid(user_id),
        )

        db.add(activity)

        if commit:
            db.commit()
            db.refresh(activity)

        return activity

    @staticmethod
    def get_activities(invoice_id: int, current_user: dict, db: Session) -> list[dict]:
        """The invoice's history, newest first, with the sales order's (and
        through it the opportunity's) entries merged in on read."""

        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        rows: list[dict] = [
            {
                "id": f"pi-{a.id}",
                "source": "proforma_invoice",
                "action": a.action,
                "description": a.description,
                "from_status": a.from_status,
                "to_status": a.to_status,
                "created_by": str(a.created_by) if a.created_by else None,
                "created_at": a.created_at,
            }
            for a in db.query(ProformaInvoiceActivity)
            .filter(ProformaInvoiceActivity.proforma_invoice_id == invoice.id)
            .all()
        ]

        if invoice.sales_order_id:
            try:
                rows.extend(
                    SalesOrderService.get_activities(
                        invoice.sales_order_id,
                        current_user,
                        db,
                    )
                )
            except HTTPException:
                # The order was deleted, or belongs to someone this user
                # cannot see; the invoice's own history still stands.
                pass

        rows.sort(key=lambda row: row["created_at"], reverse=True)

        return rows

    @staticmethod
    def log_activity(invoice_id: int, request, current_user: dict, db: Session):
        invoice = ProformaInvoiceService.get_by_id(invoice_id, db)

        ProformaInvoiceService.assert_can_edit(invoice, current_user, db)

        remarks = (getattr(request, "remarks", None) or "").strip()
        raw_status = getattr(request, "status", None)

        if not raw_status and not remarks:
            raise HTTPException(
                status_code=400,
                detail="Choose a status or write remarks before logging the activity.",
            )

        if raw_status:
            invoice = ProformaInvoiceService._move(
                invoice,
                raw_status,
                current_user,
                db,
                description=remarks,
            )

            activity = (
                db.query(ProformaInvoiceActivity)
                .filter(ProformaInvoiceActivity.proforma_invoice_id == invoice.id)
                .order_by(ProformaInvoiceActivity.id.desc())
                .first()
            )

            return invoice, activity

        activity = ProformaInvoiceService.record_activity(
            db,
            invoice,
            action=(getattr(request, "action", None) or "").strip() or "Note Logged",
            description=remarks,
            user_id=current_user.get("user_id"),
        )

        return invoice, activity


def serialize_proforma_invoice(invoice: ProformaInvoice) -> dict:
    order: SalesOrder | None = invoice.sales_order

    advance_percent = (
        invoice.advance_percent if invoice.advance_percent is not None else 30.0
    )
    grand_total = invoice.grand_total or 0.0
    advance_expected = round(grand_total * advance_percent / 100.0, 2)

    def iso(value):
        return value.isoformat() if value else None

    return {
        "id": invoice.id,
        "pi_number": invoice.pi_number,
        "status": invoice.status,
        "sales_order_id": invoice.sales_order_id,
        # What the Order Process and Attached Documents panels need to know
        # about the order, without a second request.
        "sales_order": (
            {
                "id": order.id,
                "order_number": order.order_number,
                "status": order.status,
                "quotation_id": order.quotation_id,
                "po_number": order.po_number,
                "po_date": iso(order.po_date),
                "opportunity_id": order.opportunity_id,
            }
            if order
            else None
        ),
        "issue_date": iso(invoice.issue_date),
        "due_date": iso(invoice.due_date),
        "assigned_to": invoice.assigned_to,
        "customer_name": invoice.customer_name,
        "company_name": invoice.company_name,
        "customer_type": invoice.customer_type,
        "state": invoice.state,
        "customer_information": invoice.customer_information or {},
        "billing_address": invoice.billing_address or {},
        "shipping_address": invoice.shipping_address or {},
        "items": invoice.items or [],
        "total_amount": invoice.total_amount or 0.0,
        "discount_amount": invoice.discount_amount or 0.0,
        # How the order's summary discount and ORC were entered, so the form
        # can total a line change exactly as the backend will on save.
        "discount_mode": invoice.discount_mode or "AMOUNT",
        "discount_input": invoice.discount_input,
        "orc_mode": invoice.orc_mode or "AMOUNT",
        "orc_input": invoice.orc_input,
        "orc_amount": invoice.orc_amount or 0.0,
        "orc_percent": invoice.orc_percent or 0.0,
        "freight_charges": invoice.freight_charges or 0.0,
        "installation_lumpsum": invoice.installation_lumpsum or 0.0,
        "taxable_amount": invoice.taxable_amount or 0.0,
        "gst_percent": invoice.gst_percent if invoice.gst_percent is not None else 18.0,
        "gst_amount": invoice.gst_amount or 0.0,
        "grand_total": grand_total,
        "amount_paid": invoice.amount_paid or 0.0,
        "balance_due": invoice.balance_due or 0.0,
        "advance_percent": advance_percent,
        "advance_expected": advance_expected,
        "balance_expected": round(grand_total - advance_expected, 2),
        "commercial_terms": invoice.commercial_terms or [],
        "technical_notes": invoice.technical_notes,
        "attachments": invoice.attachments or [],
        "generated_at": iso(invoice.generated_at),
        "sent_at": iso(invoice.sent_at),
        "sent_to": invoice.sent_to,
        "send_options": invoice.send_options or {},
        "creator_id": str(invoice.creator_id) if invoice.creator_id else None,
        "creator_name": invoice.creator_name,
        "created_at": iso(invoice.created_at),
        "updated_at": iso(invoice.updated_at),
    }
