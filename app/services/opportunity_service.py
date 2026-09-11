from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.workflow_status import (
    LEAD_TRANSITIONS,
    OPPORTUNITY_TRANSITIONS,
    LeadStatus,
    OpportunityStatus,
    assert_transition,
    normalize_lead_status,
)
from app.models.opportunity import Opportunity
from app.models.opportunity_activity import OpportunityActivity
from app.repositories.opportunity_repository import OpportunityRepository
from app.services.lead_service import LeadService, get_visible_creator_user_ids

#: Headline written onto the activity entry when an opportunity reaches a
#: status. Phrased as what the user did, because that is how the timeline
#: reads back.
OPPORTUNITY_STATUS_ACTIONS: dict[str, str] = {
    OpportunityStatus.QUALIFICATION: "Opportunity Created",
    OpportunityStatus.REQUIREMENT: "Moved to Requirement",
    OpportunityStatus.DEMO: "Moved to Demo",
    OpportunityStatus.PROPOSAL: "Moved to Proposal Sent",
    OpportunityStatus.NEGOTIATION: "Moved to Negotiation",
    OpportunityStatus.WON: "Marked as Won",
    OpportunityStatus.LOST: "Marked as Dead",
}


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_product_totals(items) -> dict:
    """Derive the Products & Order Items figures from the lines.

    Discount is a per-line percentage off that line, and tax is charged on
    what is left after it:

        line        = qty * unit price
        discount    = line * discount%
        taxable     = line - discount
        tax         = taxable * tax%
        total       = SUM(taxable + tax)
    """

    subtotal = 0.0
    discount_amount = 0.0
    tax_amount = 0.0

    for raw in items or []:
        item = raw if isinstance(raw, dict) else dict(raw)

        quantity = _as_float(item.get("quantity"), 1.0)
        unit_price = _as_float(item.get("unit_price") or item.get("unitPrice"))

        line = quantity * unit_price
        discount = line * _as_float(item.get("discount")) / 100.0
        taxable = line - discount

        subtotal += line
        discount_amount += discount
        tax_amount += taxable * _as_float(item.get("tax")) / 100.0

    total = subtotal - discount_amount + tax_amount

    return {
        "products_subtotal": round(subtotal, 2),
        "products_discount_amount": round(discount_amount, 2),
        "products_tax_amount": round(tax_amount, 2),
        "products_total": round(total, 2),
    }


def _to_uuid(value) -> UUID | None:
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class OpportunityService:

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    def get_visible(current_user: dict, db: Session) -> list[Opportunity]:
        visible_ids = get_visible_creator_user_ids(current_user, db)

        return OpportunityRepository.get_visible(
            db,
            visible_ids,
            current_user.get("user_id"),
        )

    @staticmethod
    def get_by_id(opportunity_id: int, db: Session) -> Opportunity:
        opportunity = OpportunityRepository.get_by_id(db, opportunity_id)

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        return opportunity

    # ------------------------------------------------------------------
    # Authorisation
    # ------------------------------------------------------------------
    @staticmethod
    def assert_can_edit(
        opportunity: Opportunity,
        current_user: dict,
        db: Session,
    ) -> None:
        """Mirror the Lead authorisation rule: creator, assignee or a
        reporting superior may modify the record."""

        if current_user.get("is_super_admin", False):
            return

        user_id = current_user.get("user_id")

        if str(opportunity.creator_id) == user_id:
            return

        if opportunity.assigned_to_id and str(opportunity.assigned_to_id) == user_id:
            return

        visible_ids = get_visible_creator_user_ids(current_user, db)

        if visible_ids and str(opportunity.creator_id) in visible_ids:
            return

        raise HTTPException(
            status_code=403,
            detail=(
                "Only the opportunity creator, assigned user, and their "
                "reporting superiors can modify this opportunity"
            ),
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    @staticmethod
    def create(
        request,
        creator_id: UUID,
        db: Session,
        current_user: dict | None = None,
    ) -> Opportunity:
        # Creating against a lead is a conversion: reuse that path so the
        # lead is always marked CONVERTED and never left dangling.
        if getattr(request, "lead_id", None):
            return OpportunityService.convert_lead(
                request.lead_id,
                request,
                current_user or {"user_id": str(creator_id), "is_super_admin": True},
                db,
            )

        status = request.status or OpportunityStatus.QUALIFICATION

        if status not in OpportunityStatus.ALL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid opportunity status '{status}'. "
                    f"Expected one of: {', '.join(OpportunityStatus.ALL)}."
                ),
            )

        title = (
            request.title
            or request.organization_name
            or request.contact_name
            or "New Opportunity"
        )

        opportunity = Opportunity(
            lead_id=request.lead_id,
            title=title,
            description=request.description,
            status=status,
            deal_value=request.deal_value or 0.0,
            priority=request.priority or "Medium",
            expected_closing_date=request.expected_closing_date,
            contact_name=request.contact_name,
            organization_name=request.organization_name,
            email=request.email,
            mobile_number=request.mobile_number,
            website=request.website,
            designation=request.designation,
            office_address=request.office_address,
            city=request.city,
            zip_code=request.zip_code,
            country=request.country or "India",
            gst_number=request.gst_number,
            pan_number=request.pan_number,
            coi_number=request.coi_number,
            shipping_address=request.shipping_address,
            shipping_city=request.shipping_city,
            shipping_state=request.shipping_state,
            shipping_zip_code=request.shipping_zip_code,
            shipping_country=request.shipping_country,
            requirements=request.requirements,
            remarks=request.remarks,
            demo_status=request.demo_status or "none",
            product_items=request.product_items,
            **compute_product_totals(request.product_items),
            lead_source=request.lead_source,
            purchase_timeline=request.purchase_timeline,
            attachments=request.attachments,
            compliance_documents=request.compliance_documents,
            customer_type_id=request.customer_type_id,
            state_id=request.state_id,
            creator_id=creator_id,
            assigned_to_id=_to_uuid(request.assigned_to_id),
        )

        opportunity = OpportunityRepository.create(db, opportunity)

        # Opens the timeline with the event that started it, so a brand new
        # opportunity shows real history rather than an empty panel.
        OpportunityService.record_activity(
            db,
            opportunity,
            action=OPPORTUNITY_STATUS_ACTIONS.get(
                status,
                "Opportunity Created",
            ),
            description=request.remarks or request.requirements,
            to_status=status,
            user_id=str(creator_id),
        )

        return opportunity

    @staticmethod
    def update(
        opportunity_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> Opportunity:
        opportunity = OpportunityService.get_by_id(opportunity_id, db)

        OpportunityService.assert_can_edit(opportunity, current_user, db)

        simple_fields = [
            "title",
            "description",
            "deal_value",
            "priority",
            "expected_closing_date",
            "contact_name",
            "organization_name",
            "email",
            "mobile_number",
            "website",
            "designation",
            "office_address",
            "city",
            "zip_code",
            "country",
            "gst_number",
            "pan_number",
            "coi_number",
            "shipping_address",
            "shipping_city",
            "shipping_state",
            "shipping_zip_code",
            "shipping_country",
            "requirements",
            "remarks",
            "demo_status",
            "product_items",
            "lead_source",
            "purchase_timeline",
            "attachments",
            "compliance_documents",
            "customer_type_id",
            "state_id",
        ]

        for field in simple_fields:
            value = getattr(request, field, None)
            if value is not None:
                setattr(opportunity, field, value)

        if getattr(request, "product_items", None) is not None:
            for key, value in compute_product_totals(
                opportunity.product_items
            ).items():
                setattr(opportunity, key, value)

        if getattr(request, "assigned_to_id", None) is not None:
            opportunity.assigned_to_id = _to_uuid(request.assigned_to_id)

        return OpportunityRepository.save(db, opportunity)

    # ------------------------------------------------------------------
    # Activity History
    # ------------------------------------------------------------------
    @staticmethod
    def record_activity(
        db: Session,
        opportunity: Opportunity,
        action: str,
        description: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        user_id: str | None = None,
        commit: bool = True,
    ) -> OpportunityActivity:
        """Append one entry to an opportunity's Activity History.

        A single helper so every path that changes an opportunity - the status
        endpoint behind the row and board menus, the Log Activity form,
        creation, conversion - writes history the same way.
        """

        activity = OpportunityActivity(
            opportunity_id=opportunity.id,
            action=action,
            description=(description or None),
            from_status=from_status,
            to_status=to_status,
            created_by=_to_uuid(user_id) if user_id else None,
        )

        db.add(activity)

        if commit:
            db.commit()
            db.refresh(activity)

        return activity

    @staticmethod
    def get_activities(
        opportunity_id: int,
        current_user: dict,
        db: Session,
    ) -> list[OpportunityActivity]:
        """Newest-first history for one opportunity, for the details drawer."""

        opportunity = OpportunityService.get_by_id(opportunity_id, db)

        # Same rule as every other drawer action: whoever may modify the
        # opportunity may read its history.
        OpportunityService.assert_can_edit(opportunity, current_user, db)

        return (
            db.query(OpportunityActivity)
            .filter(OpportunityActivity.opportunity_id == opportunity.id)
            .order_by(
                OpportunityActivity.created_at.desc(),
                OpportunityActivity.id.desc(),
            )
            .all()
        )

    @staticmethod
    def log_activity(
        opportunity_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> tuple[Opportunity, OpportunityActivity]:
        """Record an activity, moving the stage when one was chosen.

        This is what the Log Activity form posts to. The move and the note go
        in together so a stage change always carries the reason it happened.
        """

        opportunity = OpportunityService.get_by_id(opportunity_id, db)

        OpportunityService.assert_can_edit(opportunity, current_user, db)

        remarks = (getattr(request, "remarks", None) or "").strip()
        raw_status = getattr(request, "status", None)

        if not raw_status and not remarks:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Choose a status or write remarks before logging the "
                    "activity."
                ),
            )

        current_status = opportunity.status
        action = (getattr(request, "action", None) or "").strip()
        target = None

        if raw_status:
            target = str(raw_status).upper()

            if target not in OpportunityStatus.ALL:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid opportunity status '{raw_status}'. "
                        f"Expected one of: {', '.join(OpportunityStatus.ALL)}."
                    ),
                )

            assert_transition(
                "opportunity",
                OPPORTUNITY_TRANSITIONS,
                current_status,
                target,
            )

            opportunity.status = target

            # Won is recorded on the opportunity itself, the same way the
            # status endpoint does it - the two must not disagree.
            if target == OpportunityStatus.WON:
                opportunity.won_at = datetime.utcnow()
                opportunity.won_by = _to_uuid(current_user.get("user_id"))
                if remarks:
                    opportunity.won_reason = remarks

            if target == OpportunityStatus.LOST and remarks:
                opportunity.lost_reason = remarks

            if not action:
                action = OPPORTUNITY_STATUS_ACTIONS.get(target, "Status Updated")
        elif not action:
            action = "Note Logged"

        activity = OpportunityService.record_activity(
            db,
            opportunity,
            action=action,
            description=remarks,
            from_status=current_status,
            to_status=target,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.add(opportunity)
        db.commit()
        db.refresh(opportunity)
        db.refresh(activity)

        return opportunity, activity

    @staticmethod
    def update_status(
        opportunity_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> Opportunity:
        opportunity = OpportunityService.get_by_id(opportunity_id, db)

        OpportunityService.assert_can_edit(opportunity, current_user, db)

        target = str(request.status or "").upper()

        if target not in OpportunityStatus.ALL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid opportunity status '{request.status}'. "
                    f"Expected one of: {', '.join(OpportunityStatus.ALL)}."
                ),
            )

        previous_status = opportunity.status

        assert_transition(
            "opportunity",
            OPPORTUNITY_TRANSITIONS,
            previous_status,
            target,
        )

        opportunity.status = target

        # The row, board and drawer menus all move opportunities through this
        # endpoint, so it has to leave the same trail the Log Activity form
        # does - otherwise most stage changes would be missing from history.
        if target != previous_status:
            OpportunityService.record_activity(
                db,
                opportunity,
                action=OPPORTUNITY_STATUS_ACTIONS.get(target, "Status Updated"),
                description=getattr(request, "remarks", None),
                from_status=previous_status,
                to_status=target,
                user_id=current_user.get("user_id"),
                commit=False,
            )

        # "Deal Won" is recorded on the opportunity itself, not a separate entity.
        if target == OpportunityStatus.WON:
            opportunity.won_at = datetime.utcnow()
            opportunity.won_by = _to_uuid(current_user.get("user_id"))
            if getattr(request, "won_reason", None):
                opportunity.won_reason = request.won_reason

        if target == OpportunityStatus.LOST and getattr(request, "lost_reason", None):
            opportunity.lost_reason = request.lost_reason

        return OpportunityRepository.save(db, opportunity)

    # ------------------------------------------------------------------
    # Lead -> Opportunity conversion
    # ------------------------------------------------------------------
    @staticmethod
    def convert_lead(
        lead_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> Opportunity:
        """Promote a Lead to an Opportunity and mark the Lead CONVERTED."""

        from app.models.lead import Lead

        lead = db.query(Lead).filter(Lead.id == lead_id).first()

        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        LeadService.assert_can_modify_lead(lead, current_user, db)

        existing = OpportunityRepository.get_by_lead_id(db, lead_id)

        if existing is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Lead {lead_id} has already been converted to "
                    f"opportunity {existing.id}."
                ),
            )

        current_status = normalize_lead_status(lead.status)

        if current_status == LeadStatus.LOST:
            raise HTTPException(
                status_code=400,
                detail="A lost lead cannot be converted to an opportunity.",
            )

        if current_status == LeadStatus.CONVERTED:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This lead has already been converted to an opportunity."
                ),
            )

        # Qualification is the gate into the opportunity pipeline: a lead must
        # pass the qualification checklist before it can be converted.
        if current_status != LeadStatus.QUALIFIED:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Lead must be {LeadStatus.QUALIFIED} before conversion "
                    f"(currently {current_status}). Mark it as Contacted and "
                    f"then Qualified first."
                ),
            )

        def field(name, fallback=None):
            """Form value when supplied, otherwise the lead's own value."""

            value = getattr(request, name, None)
            return value if value not in (None, "") else fallback

        opportunity = Opportunity(
            lead_id=lead.id,
            title=field("title", lead.title),
            description=field("description", lead.description),
            status=OpportunityStatus.QUALIFICATION,
            deal_value=field("deal_value", 0.0) or 0.0,
            priority=field("priority", "Medium"),
            expected_closing_date=field("expected_closing_date"),
            contact_name=field("contact_name", lead.contact_name),
            organization_name=field("organization_name", lead.organization_name),
            email=field("email", lead.email),
            mobile_number=field("mobile_number", lead.mobile_number),
            website=field("website", lead.website),
            designation=field("designation", lead.designation),
            office_address=field("office_address", lead.office_address),
            city=field("city", lead.city),
            zip_code=field("zip_code", lead.zip_code),
            country=field("country", lead.country),
            gst_number=field("gst_number", lead.gst_number),
            pan_number=field("pan_number", lead.pan_number),
            coi_number=field("coi_number", lead.coi_number),
            requirements=field("requirements", lead.requirements),
            remarks=field("remarks", lead.remarks),
            demo_status=field("demo_status", lead.demo_status) or "none",
            product_items=field("product_items", lead.quotation_items),
            **compute_product_totals(
                field("product_items", lead.quotation_items)
            ),
            lead_source=field(
                "lead_source",
                lead.lead_source.name if lead.lead_source else None,
            ),
            purchase_timeline=field("purchase_timeline", None),
            attachments=field("attachments", None),
            compliance_documents=field("compliance_documents", None),
            customer_type_id=field("customer_type_id", lead.customer_type_id),
            state_id=field("state_id", lead.state_id),
            creator_id=lead.creator_id,
            assigned_to_id=(
                _to_uuid(getattr(request, "assigned_to_id", None))
                or lead.assigned_to_id
            ),
        )

        opportunity = OpportunityRepository.create(db, opportunity)

        # The opportunity's own timeline starts where the lead's ended, so it
        # opens with the conversion rather than an empty panel.
        OpportunityService.record_activity(
            db,
            opportunity,
            action="Converted From Lead",
            description=f"Raised from lead #{lead.id} - {lead.title}.",
            to_status=OpportunityStatus.QUALIFICATION,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        # QUALIFIED -> CONVERTED is the only move left, but validate it
        # against the state machine rather than assigning blindly.
        assert_transition(
            "lead", LEAD_TRANSITIONS, current_status, LeadStatus.CONVERTED
        )

        lead.status = LeadStatus.CONVERTED
        lead.stage = "opportunity"
        db.add(lead)

        # Conversion is the last thing that happens to a lead, so it has to
        # close out its timeline rather than ending on "Marked as Qualified".
        LeadService.record_activity(
            db,
            lead,
            action="Converted to Opportunity",
            description=f"Opportunity #{opportunity.id} - {opportunity.title}.",
            from_status=current_status,
            to_status=LeadStatus.CONVERTED,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.commit()
        db.refresh(lead)

        return opportunity


def serialize_opportunity(opportunity: Opportunity) -> dict:
    """Shape an Opportunity for the API, matching the existing response style."""

    return {
        "id": opportunity.id,
        "lead_id": opportunity.lead_id,
        "title": opportunity.title,
        "description": opportunity.description,
        "status": opportunity.status,
        "deal_value": opportunity.deal_value,
        "priority": opportunity.priority,
        "expected_closing_date": (
            opportunity.expected_closing_date.isoformat()
            if opportunity.expected_closing_date
            else None
        ),
        "contact_name": opportunity.contact_name,
        "organization_name": opportunity.organization_name,
        "email": opportunity.email,
        "mobile_number": opportunity.mobile_number,
        "website": opportunity.website,
        "designation": opportunity.designation,
        "office_address": opportunity.office_address,
        "city": opportunity.city,
        "zip_code": opportunity.zip_code,
        "country": opportunity.country,
        "gst_number": opportunity.gst_number,
        "pan_number": opportunity.pan_number,
        "coi_number": opportunity.coi_number,
        "shipping_address": opportunity.shipping_address,
        "shipping_city": opportunity.shipping_city,
        "shipping_state": opportunity.shipping_state,
        "shipping_zip_code": opportunity.shipping_zip_code,
        "shipping_country": opportunity.shipping_country,
        "requirements": opportunity.requirements,
        "remarks": opportunity.remarks,
        "demo_status": opportunity.demo_status,
        "product_items": opportunity.product_items,
        "products_subtotal": opportunity.products_subtotal or 0.0,
        "products_discount_amount": opportunity.products_discount_amount or 0.0,
        "products_tax_amount": opportunity.products_tax_amount or 0.0,
        "products_total": opportunity.products_total or 0.0,
        "lead_source": opportunity.lead_source,
        "purchase_timeline": opportunity.purchase_timeline,
        "attachments": opportunity.attachments or [],
        "compliance_documents": opportunity.compliance_documents or {},
        "customer_type_id": opportunity.customer_type_id,
        "customer_type_name": (
            opportunity.customer_type.name if opportunity.customer_type else None
        ),
        "state_id": opportunity.state_id,
        "state_name": opportunity.state.name if opportunity.state else None,
        "creator_id": str(opportunity.creator_id) if opportunity.creator_id else None,
        "assigned_to_id": (
            str(opportunity.assigned_to_id) if opportunity.assigned_to_id else None
        ),
        "won_at": opportunity.won_at.isoformat() if opportunity.won_at else None,
        "won_by": str(opportunity.won_by) if opportunity.won_by else None,
        "won_reason": opportunity.won_reason,
        "lost_reason": opportunity.lost_reason,
        "created_at": (
            opportunity.created_at.isoformat() if opportunity.created_at else None
        ),
        "updated_at": (
            opportunity.updated_at.isoformat() if opportunity.updated_at else None
        ),
    }
