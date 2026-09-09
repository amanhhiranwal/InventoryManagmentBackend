from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.workflow_status import (
    QUOTATION_TRANSITIONS,
    OpportunityStatus,
    QuotationStatus,
    assert_transition,
    normalize_quotation_status,
)
from app.models.quotation import Quotation
from app.repositories.opportunity_repository import OpportunityRepository
from app.repositories.quotation_repository import QuotationRepository
from app.services.email_service import EmailService
from app.services.lead_service import get_visible_creator_user_ids

#: Offer validity shown on the form as "Validation Date (30 days)".
DEFAULT_VALIDITY_DAYS = 30

#: Tags the rich-text composer can produce. Anything else is stripped, so a
#: pasted <script>/<iframe>/on* handler can never reach a recipient's inbox.
ALLOWED_EMAIL_TAGS = {
    "b", "strong", "i", "em", "u", "br", "p", "div", "span",
    "ul", "ol", "li", "a", "h1", "h2", "h3", "h4", "blockquote",
}

#: Attributes kept on those tags. "style" is deliberately excluded.
ALLOWED_EMAIL_ATTRS = {"href", "title"}


def sanitize_email_html(raw: str) -> str:
    """Strip everything but simple formatting from composer HTML."""

    import html as html_module
    from html.parser import HTMLParser

    class Cleaner(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []
            self.skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "iframe", "object", "embed"):
                self.skip_depth += 1
                return

            if self.skip_depth or tag not in ALLOWED_EMAIL_TAGS:
                return

            kept = []
            for name, value in attrs:
                if name.lower() not in ALLOWED_EMAIL_ATTRS or not value:
                    continue
                if name.lower() == "href" and not value.lower().startswith(
                    ("http://", "https://", "mailto:")
                ):
                    continue
                kept.append(f' {name}="{html_module.escape(value, quote=True)}"')

            self.out.append(f"<{tag}{''.join(kept)}>")

        def handle_startendtag(self, tag, attrs):
            if not self.skip_depth and tag in ALLOWED_EMAIL_TAGS:
                self.out.append(f"<{tag}/>")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "iframe", "object", "embed"):
                self.skip_depth = max(0, self.skip_depth - 1)
                return

            if not self.skip_depth and tag in ALLOWED_EMAIL_TAGS:
                self.out.append(f"</{tag}>")

        def handle_data(self, data):
            if not self.skip_depth:
                self.out.append(html_module.escape(data))

    cleaner = Cleaner()
    cleaner.feed(raw or "")
    cleaner.close()

    return "".join(cleaner.out)


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _item_dict(item) -> dict:
    """Accept either a Pydantic QuotationItem or a plain dict."""

    if hasattr(item, "model_dump"):
        return item.model_dump()

    if hasattr(item, "dict"):
        return item.dict()

    return dict(item or {})


def compute_totals(
    items: list,
    orc_percent: float = 0.0,
    orc_amount: float = 0.0,
    freight_charges: float = 0.0,
    installation_lumpsum: float = 0.0,
    gst_percent: float = 18.0,
    advance_percent: float = 30.0,
) -> dict:
    """Derive every money figure on the quotation from its line items.

    Kept in one place, and always recomputed on write, so the totals stored
    on the row cannot drift away from the lines they came from.

        subtotal        = SUM(qty * unit_price)
        discount        = SUM(line subtotal * discount%)
        taxable         = subtotal - discount + ORC + freight + installation
        gst             = taxable * gst%
        total payable   = taxable + gst
    """

    subtotal = 0.0
    discount_amount = 0.0

    for raw in items or []:
        item = _item_dict(raw)

        quantity = _as_float(item.get("quantity"), 1.0)
        unit_price = _as_float(item.get("unit_price"))
        discount_pct = _as_float(item.get("discount"))

        line_total = quantity * unit_price

        subtotal += line_total
        discount_amount += line_total * discount_pct / 100.0

    orc_percent = _as_float(orc_percent)
    orc_amount = _as_float(orc_amount)

    # An explicit ORC amount wins; otherwise derive it from the percentage.
    # Whichever way round, the other value is back-filled so the UI can label
    # the row "ORC (1.02%)" against a flat amount.
    if orc_amount:
        if subtotal:
            orc_percent = orc_amount / subtotal * 100.0
    elif orc_percent:
        orc_amount = subtotal * orc_percent / 100.0

    freight_charges = _as_float(freight_charges)
    installation_lumpsum = _as_float(installation_lumpsum)

    taxable_amount = (
        subtotal
        - discount_amount
        + orc_amount
        + freight_charges
        + installation_lumpsum
    )

    gst_percent = _as_float(gst_percent, 18.0)
    gst_amount = taxable_amount * gst_percent / 100.0

    total_payable = taxable_amount + gst_amount

    advance_percent = _as_float(advance_percent, 30.0)
    advance_amount = total_payable * advance_percent / 100.0

    return {
        "subtotal": round(subtotal, 2),
        "discount_amount": round(discount_amount, 2),
        "orc_percent": round(orc_percent, 4),
        "orc_amount": round(orc_amount, 2),
        "freight_charges": round(freight_charges, 2),
        "installation_lumpsum": round(installation_lumpsum, 2),
        "taxable_amount": round(taxable_amount, 2),
        "gst_percent": round(gst_percent, 2),
        "gst_amount": round(gst_amount, 2),
        "total_payable": round(total_payable, 2),
        "advance_percent": round(advance_percent, 2),
        "advance_amount": round(advance_amount, 2),
        "on_delivery_amount": round(total_payable - advance_amount, 2),
    }


def serialize_quotation(quotation: Quotation) -> dict:
    if quotation is None:
        return {}

    return {
        "id": quotation.id,
        "quote_number": quotation.quote_number,
        "opportunity_id": quotation.opportunity_id,
        "status": quotation.status,

        "opportunity_name": quotation.opportunity_name,
        "organization_name": quotation.organization_name,
        "contact_name": quotation.contact_name,
        "designation": quotation.designation,
        "email": quotation.email,
        "mobile_number": quotation.mobile_number,
        "customer_type": quotation.customer_type,

        "quotation_date": (
            quotation.quotation_date.isoformat()
            if quotation.quotation_date
            else None
        ),
        "validation_date": (
            quotation.validation_date.isoformat()
            if quotation.validation_date
            else None
        ),

        "billing_address": quotation.billing_address or {},
        "shipping_address": quotation.shipping_address or {},
        "shipping_same_as_billing": bool(quotation.shipping_same_as_billing),

        "items": quotation.items or [],

        "subtotal": quotation.subtotal or 0.0,
        "discount_amount": quotation.discount_amount or 0.0,
        "orc_percent": quotation.orc_percent or 0.0,
        "orc_amount": quotation.orc_amount or 0.0,
        "freight_charges": quotation.freight_charges or 0.0,
        "installation_lumpsum": quotation.installation_lumpsum or 0.0,
        "taxable_amount": quotation.taxable_amount or 0.0,
        "gst_percent": quotation.gst_percent or 0.0,
        "gst_amount": quotation.gst_amount or 0.0,
        "total_payable": quotation.total_payable or 0.0,
        "advance_percent": quotation.advance_percent or 0.0,
        "advance_amount": quotation.advance_amount or 0.0,
        "on_delivery_amount": quotation.on_delivery_amount or 0.0,

        "attachments": quotation.attachments or [],
        "terms": quotation.terms or [],
        "remarks": quotation.remarks,

        "sent_at": quotation.sent_at.isoformat() if quotation.sent_at else None,
        "sent_to": quotation.sent_to,
        "sent_cc": quotation.sent_cc,
        "sent_subject": quotation.sent_subject,
        "send_options": quotation.send_options or {},
        "rejected_reason": quotation.rejected_reason,

        "customer_type_id": quotation.customer_type_id,
        "state_id": quotation.state_id,
        "state_name": quotation.state.name if quotation.state else None,

        "creator_id": str(quotation.creator_id) if quotation.creator_id else None,
        "assigned_to_id": (
            str(quotation.assigned_to_id) if quotation.assigned_to_id else None
        ),

        "created_at": (
            quotation.created_at.isoformat() if quotation.created_at else None
        ),
        "updated_at": (
            quotation.updated_at.isoformat() if quotation.updated_at else None
        ),
    }


class QuotationService:

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    @staticmethod
    def get_visible(current_user: dict, db: Session) -> list[Quotation]:
        visible_ids = get_visible_creator_user_ids(current_user, db)

        return QuotationRepository.get_visible(
            db,
            visible_ids,
            current_user.get("user_id"),
        )

    @staticmethod
    def get_by_id(quotation_id: int, db: Session) -> Quotation:
        quotation = QuotationRepository.get_by_id(db, quotation_id)

        if quotation is None:
            raise HTTPException(status_code=404, detail="Quotation not found")

        return quotation

    @staticmethod
    def assert_can_modify(quotation: Quotation, current_user: dict, db: Session):
        if current_user.get("is_super_admin", False):
            return

        user_id = current_user.get("user_id")

        if str(quotation.creator_id) == user_id:
            return

        if quotation.assigned_to_id and str(quotation.assigned_to_id) == user_id:
            return

        visible_ids = get_visible_creator_user_ids(current_user, db)

        if visible_ids and str(quotation.creator_id) in visible_ids:
            return

        raise HTTPException(
            status_code=403,
            detail=(
                "Only the quotation creator, assigned user, and their "
                "reporting superiors can modify this quotation"
            ),
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    @staticmethod
    def create(
        request,
        creator_id: UUID,
        db: Session,
        current_user: dict | None = None,
    ) -> Quotation:

        opportunity = None

        if request.opportunity_id:
            opportunity = OpportunityRepository.get_by_id(
                db,
                request.opportunity_id,
            )

            if opportunity is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Opportunity {request.opportunity_id} was not found, "
                        "so a quotation cannot be raised against it."
                    ),
                )

        def carried(field: str, fallback=None):
            """Form value first, then the opportunity it was raised from."""

            value = getattr(request, field, None)

            if value not in (None, ""):
                return value

            if opportunity is not None:
                return getattr(opportunity, field, None) or fallback

            return fallback

        quotation_date = request.quotation_date or datetime.utcnow()
        validation_date = request.validation_date or (
            quotation_date + timedelta(days=DEFAULT_VALIDITY_DAYS)
        )

        items = [_item_dict(item) for item in (request.items or [])]

        totals = compute_totals(
            items,
            orc_percent=request.orc_percent,
            orc_amount=request.orc_amount,
            freight_charges=request.freight_charges,
            installation_lumpsum=request.installation_lumpsum,
            gst_percent=request.gst_percent,
            advance_percent=request.advance_percent,
        )

        quotation = Quotation(
            quote_number=QuotationRepository.next_quote_number(db),
            opportunity_id=request.opportunity_id,
            status=normalize_quotation_status(request.status),

            opportunity_name=(
                request.opportunity_name
                or (opportunity.title if opportunity else None)
            ),
            organization_name=carried("organization_name"),
            contact_name=carried("contact_name"),
            designation=carried("designation"),
            email=carried("email"),
            mobile_number=carried("mobile_number"),
            customer_type=request.customer_type,

            quotation_date=quotation_date,
            validation_date=validation_date,

            billing_address=request.billing_address or {},
            shipping_address=request.shipping_address or {},
            shipping_same_as_billing=bool(request.shipping_same_as_billing),

            items=items,

            attachments=request.attachments or [],
            terms=request.terms or [],
            remarks=request.remarks,

            customer_type_id=(
                request.customer_type_id
                or (opportunity.customer_type_id if opportunity else None)
            ),
            state_id=request.state_id or (opportunity.state_id if opportunity else None),

            creator_id=creator_id,
            assigned_to_id=(
                UUID(request.assigned_to_id)
                if request.assigned_to_id
                else (opportunity.assigned_to_id if opportunity else None)
            ),
            **totals,
        )

        quotation = QuotationRepository.create(db, quotation)

        # The form states this outright: "Generating this quotation will
        # automatically move Opportunity #... to Proposal / Price Quote."
        QuotationService._advance_opportunity_to_proposal(opportunity, db)

        return quotation

    @staticmethod
    def _advance_opportunity_to_proposal(opportunity, db: Session) -> None:
        """Move the opportunity to PROPOSAL when a quotation is raised.

        Only ever moves forward, and never disturbs an opportunity that is
        already past PROPOSAL or has been closed.
        """

        if opportunity is None:
            return

        pipeline = OpportunityStatus.PIPELINE

        if opportunity.status not in pipeline:
            return

        if pipeline.index(opportunity.status) >= pipeline.index(
            OpportunityStatus.PROPOSAL
        ):
            return

        opportunity.status = OpportunityStatus.PROPOSAL
        db.commit()
        db.refresh(opportunity)

    @staticmethod
    def update(
        quotation_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> Quotation:

        quotation = QuotationService.get_by_id(quotation_id, db)
        QuotationService.assert_can_modify(quotation, current_user, db)

        if quotation.status != QuotationStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Quotation {quotation.quote_number} has already been "
                    f"{quotation.status.lower()} and can no longer be edited. "
                    "Raise a revision instead."
                ),
            )

        simple_fields = [
            "opportunity_name", "organization_name", "contact_name",
            "designation", "email", "mobile_number", "customer_type",
            "quotation_date", "validation_date",
            "billing_address", "shipping_address", "shipping_same_as_billing",
            "attachments", "terms", "remarks",
            "customer_type_id", "state_id",
        ]

        for field in simple_fields:
            value = getattr(request, field, None)
            if value is not None:
                setattr(quotation, field, value)

        if request.assigned_to_id is not None:
            quotation.assigned_to_id = (
                UUID(request.assigned_to_id) if request.assigned_to_id else None
            )

        if request.items is not None:
            quotation.items = [_item_dict(item) for item in request.items]

        # Any change to lines or charges re-derives every total.
        money_changed = any(
            getattr(request, field, None) is not None
            for field in (
                "items", "orc_percent", "orc_amount", "freight_charges",
                "installation_lumpsum", "gst_percent", "advance_percent",
            )
        )

        if money_changed:
            totals = compute_totals(
                quotation.items or [],
                orc_percent=(
                    request.orc_percent
                    if request.orc_percent is not None
                    else quotation.orc_percent
                ),
                orc_amount=(
                    request.orc_amount
                    if request.orc_amount is not None
                    else 0.0
                ),
                freight_charges=(
                    request.freight_charges
                    if request.freight_charges is not None
                    else quotation.freight_charges
                ),
                installation_lumpsum=(
                    request.installation_lumpsum
                    if request.installation_lumpsum is not None
                    else quotation.installation_lumpsum
                ),
                gst_percent=(
                    request.gst_percent
                    if request.gst_percent is not None
                    else quotation.gst_percent
                ),
                advance_percent=(
                    request.advance_percent
                    if request.advance_percent is not None
                    else quotation.advance_percent
                ),
            )

            for key, value in totals.items():
                setattr(quotation, key, value)

        return QuotationRepository.save(db, quotation)

    @staticmethod
    def update_status(
        quotation_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> Quotation:

        quotation = QuotationService.get_by_id(quotation_id, db)
        QuotationService.assert_can_modify(quotation, current_user, db)

        target = normalize_quotation_status(request.status)

        assert_transition(
            "quotation",
            QUOTATION_TRANSITIONS,
            quotation.status,
            target,
        )

        quotation.status = target

        if target == QuotationStatus.REJECTED:
            quotation.rejected_reason = getattr(request, "rejected_reason", None)

        return QuotationRepository.save(db, quotation)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------
    @staticmethod
    def send(
        quotation_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> dict:
        """Email the quotation to the client and move it to SENT.

        A test send goes only to the caller and deliberately leaves the
        quotation's status alone.
        """

        quotation = QuotationService.get_by_id(quotation_id, db)
        QuotationService.assert_can_modify(quotation, current_user, db)

        recipients = [address.strip() for address in (request.to or []) if address.strip()]

        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="At least one recipient is required to send this quotation.",
            )

        cc = [address.strip() for address in (request.cc or []) if address.strip()]
        bcc = [address.strip() for address in (request.bcc or []) if address.strip()]

        subject = request.subject or (
            f"Commercial & Technical Quotation [{quotation.quote_number}]"
            f" - {quotation.opportunity_name or quotation.organization_name or ''}".strip()
        )

        body = request.body or QuotationService.default_email_body(quotation)

        if request.test_only:
            test_address = current_user.get("email")

            if not test_address:
                raise HTTPException(
                    status_code=400,
                    detail="Your account has no email address to send a test to.",
                )

            delivered = EmailService.send(
                to=test_address,
                subject=f"[TEST] {subject}",
                text_body=body,
                html_body=QuotationService.render_html(
                    quotation,
                    body,
                    getattr(request, "body_html", None),
                ),
            )

            return {
                "delivered": delivered,
                "test_only": True,
                "recipients": [test_address],
                "quotation": quotation,
            }

        delivered = EmailService.send(
            to=recipients,
            subject=subject,
            text_body=body,
            html_body=QuotationService.render_html(
                quotation,
                body,
                getattr(request, "body_html", None),
            ),
            cc=cc,
            bcc=bcc,
        )

        if not delivered:
            raise HTTPException(
                status_code=502,
                detail=(
                    "The quotation could not be emailed. Check the SMTP "
                    "settings in the backend environment and try again."
                ),
            )

        # Only recorded once the message actually went out.
        quotation.sent_at = datetime.utcnow()
        quotation.sent_by = UUID(current_user["user_id"])
        quotation.sent_to = ", ".join(recipients)[:500]
        quotation.sent_cc = ", ".join(cc)[:500] or None
        quotation.sent_bcc = ", ".join(bcc)[:500] or None
        quotation.sent_subject = subject[:500]
        quotation.sent_body = body[:8000]
        quotation.send_options = {
            "track_opens": bool(request.track_opens),
            "alert_on_download": bool(request.alert_on_download),
            "attach_gst_audit_trail": bool(request.attach_gst_audit_trail),
            "notify_lead_owner": bool(request.notify_lead_owner),
        }

        if quotation.status == QuotationStatus.DRAFT:
            quotation.status = QuotationStatus.SENT

        quotation = QuotationRepository.save(db, quotation)

        return {
            "delivered": True,
            "test_only": False,
            "recipients": recipients,
            "quotation": quotation,
        }

    @staticmethod
    def default_email_body(quotation: Quotation) -> str:
        """The message the Send dialog pre-fills, matching the design."""

        lines = [
            f"Dear {quotation.contact_name or 'Sir/Madam'},",
            "",
            f"Please find attached the official quotation "
            f"({quotation.quote_number}) for the proposed "
            f"{quotation.opportunity_name or 'requirement'}.",
            "",
            "Key Highlights:",
        ]

        for item in quotation.items or []:
            entry = _item_dict(item)
            quantity = _as_float(entry.get("quantity"), 1.0)
            name = entry.get("product") or entry.get("model") or "Item"
            lines.append(f"  - {quantity:g}x {name}")

        lines += [
            f"  - Total Value: {format_inr(quotation.total_payable)}"
            f"{_gst_clause(quotation)}",
            "",
            "Kindly review the attached quotation and let us know if you "
            "require any adjustments or technical clarifications.",
            "",
            "Warm regards,",
        ]

        return "\n".join(lines)

    @staticmethod
    def render_html(
        quotation: Quotation,
        body: str,
        body_html: str | None = None,
    ) -> str:
        """Build the text/html part of the message.

        When the composer supplies rich text it is sanitised and used as-is
        so formatting survives; otherwise the plain-text body is escaped and
        its newlines turned into breaks.
        """

        if body_html and body_html.strip():
            inner = sanitize_email_html(body_html)
        else:
            inner = (
                body.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br/>")
            )

        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;'
            'font-size:14px;color:#1e293b;line-height:1.6">'
            f"{inner}"
            "</div>"
        )


def format_inr(amount: float | None) -> str:
    """Format in the Indian grouping used across the UI, e.g. 57,61,940."""

    value = int(round(_as_float(amount)))

    negative = value < 0
    digits = str(abs(value))

    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []

        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]

        if head:
            groups.insert(0, head)

        digits = ",".join(groups + [tail])

    return f"{'-' if negative else ''}Rs {digits}"


def _gst_clause(quotation: Quotation) -> str:
    """How the total's tax is described in the default email body.

    Total Payable is the taxable amount plus GST, so the figure really is
    inclusive; state the amount and rate actually held on the quotation
    rather than assuming 18%, and say nothing when no GST applies.
    """

    percent = _as_float(quotation.gst_percent)

    if percent <= 0:
        return " (GST not applicable)"

    rate = f"{percent:g}%"
    amount = _as_float(quotation.gst_amount)

    if amount > 0:
        return f" (inclusive of {format_inr(amount)} GST at {rate})"

    return f" (inclusive of {rate} GST)"
