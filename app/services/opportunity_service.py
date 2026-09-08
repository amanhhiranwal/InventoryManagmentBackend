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
from app.repositories.opportunity_repository import OpportunityRepository
from app.services.lead_service import LeadService, get_visible_creator_user_ids


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
            requirements=request.requirements,
            remarks=request.remarks,
            demo_status=request.demo_status or "none",
            product_items=request.product_items,
            customer_type_id=request.customer_type_id,
            state_id=request.state_id,
            creator_id=creator_id,
            assigned_to_id=_to_uuid(request.assigned_to_id),
        )

        return OpportunityRepository.create(db, opportunity)

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
            "requirements",
            "remarks",
            "demo_status",
            "product_items",
            "customer_type_id",
            "state_id",
        ]

        for field in simple_fields:
            value = getattr(request, field, None)
            if value is not None:
                setattr(opportunity, field, value)

        if getattr(request, "assigned_to_id", None) is not None:
            opportunity.assigned_to_id = _to_uuid(request.assigned_to_id)

        return OpportunityRepository.save(db, opportunity)

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

        assert_transition(
            "opportunity",
            OPPORTUNITY_TRANSITIONS,
            opportunity.status,
            target,
        )

        opportunity.status = target

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
            customer_type_id=field("customer_type_id", lead.customer_type_id),
            state_id=field("state_id", lead.state_id),
            creator_id=lead.creator_id,
            assigned_to_id=(
                _to_uuid(getattr(request, "assigned_to_id", None))
                or lead.assigned_to_id
            ),
        )

        opportunity = OpportunityRepository.create(db, opportunity)

        # QUALIFIED -> CONVERTED is the only move left, but validate it
        # against the state machine rather than assigning blindly.
        assert_transition(
            "lead", LEAD_TRANSITIONS, current_status, LeadStatus.CONVERTED
        )

        lead.status = LeadStatus.CONVERTED
        lead.stage = "opportunity"
        db.add(lead)
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
        "requirements": opportunity.requirements,
        "remarks": opportunity.remarks,
        "demo_status": opportunity.demo_status,
        "product_items": opportunity.product_items,
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
