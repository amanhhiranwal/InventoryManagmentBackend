from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.workflow_status import (
    LEAD_TRANSITIONS,
    LeadStatus,
    assert_transition,
    normalize_lead_status,
)
from app.models.lead import Lead
from app.models.lead_activity import LeadActivity
from app.services.hierarchy_service import HierarchyService
from app.services.notification_service import NotificationService

#: Headline written onto the activity entry when a lead reaches a status.
#: Phrased as what the user did, because that is what the timeline reads as.
LEAD_STATUS_ACTIONS: dict[str, str] = {
    LeadStatus.NEW: "Lead Created",
    LeadStatus.CONTACTED: "Marked as Contacted",
    LeadStatus.QUALIFIED: "Marked as Qualified",
    LeadStatus.CONVERTED: "Converted to Opportunity",
    LeadStatus.LOST: "Marked as Dead",
}

#: Legacy Lead.stage that goes with each canonical status. The two columns are
#: kept in step here so callers no longer have to send both and risk them
#: disagreeing.
LEAD_STATUS_STAGES: dict[str, str] = {
    LeadStatus.NEW: "lead",
    LeadStatus.CONTACTED: "lead",
    LeadStatus.QUALIFIED: "lead",
    LeadStatus.CONVERTED: "opportunity",
    LeadStatus.LOST: "dead",
}

def get_visible_creator_user_ids(current_user: dict, db: Session) -> list[str]:
    """Users whose records the current user may see.

    An empty list means unrestricted (super admin). The rules live in
    HierarchyService so every module scopes the same way.
    """

    if not current_user.get("user_id"):
        return []

    visible = HierarchyService.visible_user_ids(current_user, db)
    return [] if visible is None else sorted(visible)


class LeadService:
    @staticmethod
    def assert_can_modify_lead(lead: Lead, current_user: dict, db: Session) -> None:
        """Shared authorisation check for lead mutations.

        Creator, assignee, super admin, or a reporting superior may modify.
        Extracted so Opportunity conversion applies the same rule instead of
        reimplementing it.
        """

        if current_user.get("is_super_admin", False):
            return

        user_id = current_user.get("user_id")

        if str(lead.creator_id) == user_id:
            return

        if lead.assigned_to_id and str(lead.assigned_to_id) == user_id:
            return

        visible_ids = get_visible_creator_user_ids(current_user, db)

        if visible_ids and (
            str(lead.creator_id) in visible_ids
            or str(lead.assigned_to_id) in visible_ids
        ):
            return

        raise HTTPException(
            status_code=403,
            detail=(
                "Only the lead creator, assigned user, and their reporting "
                "superiors can modify this lead"
            ),
        )

    @staticmethod
    def record_activity(
        db: Session,
        lead: Lead,
        action: str,
        description: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        user_id: str | None = None,
        commit: bool = True,
    ) -> LeadActivity:
        """Append one entry to a lead's Activity History.

        Kept as a single helper so every path that changes a lead - the
        progress endpoint, the Log Activity form, conversion - writes the
        history the same way instead of each inventing its own wording.
        """

        activity = LeadActivity(
            lead_id=lead.id,
            action=action,
            description=(description or None),
            from_status=from_status,
            to_status=to_status,
            created_by=UUID(user_id) if user_id else None,
        )

        db.add(activity)

        # Everyone who owns or oversees the record hears about it.
        NotificationService.notify_activity(
            db, "lead", lead, action, description, user_id
        )

        if commit:
            db.commit()
            db.refresh(activity)

        return activity

    @staticmethod
    def get_activities(lead_id: str, current_user: dict, db: Session) -> list[LeadActivity]:
        """Newest-first history for one lead, for the details drawer."""

        lead = db.query(Lead).filter(Lead.id == int(lead_id)).first()

        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        LeadService.assert_can_modify_lead(lead, current_user, db)

        return (
            db.query(LeadActivity)
            .filter(LeadActivity.lead_id == lead.id)
            .order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc())
            .all()
        )

    @staticmethod
    def log_activity(
        lead_id: str,
        request,
        current_user: dict,
        db: Session,
    ) -> tuple[Lead, LeadActivity]:
        """Record an activity, moving the lead's status when one was chosen.

        This is what the Log Activity form posts to. Doing the move and the
        note in one call is deliberate: a status change that leaves no trace of
        why it happened is the gap this whole feature exists to close.
        """

        lead = db.query(Lead).filter(Lead.id == int(lead_id)).first()

        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        LeadService.assert_can_modify_lead(lead, current_user, db)

        remarks = (getattr(request, "remarks", None) or "").strip()
        raw_status = getattr(request, "status", None)

        if not raw_status and not remarks:
            raise HTTPException(
                status_code=400,
                detail="Choose a status or write remarks before logging the activity.",
            )

        current_status = normalize_lead_status(lead.status)
        action = (getattr(request, "action", None) or "").strip()
        target_status = None

        if raw_status:
            target_status = normalize_lead_status(raw_status)

            # CONVERTED is owned by the Lead -> Opportunity conversion
            # endpoint, which also creates the opportunity. Allowing it here
            # would leave a lead marked converted with nothing to show for it.
            if target_status == LeadStatus.CONVERTED:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Use Convert To Opportunity to convert this lead; it "
                        "cannot be set from the activity log."
                    ),
                )

            assert_transition(
                "lead",
                LEAD_TRANSITIONS,
                current_status,
                target_status,
            )

            lead.status = target_status
            lead.stage = LEAD_STATUS_STAGES.get(target_status, lead.stage)

            if not action:
                action = LEAD_STATUS_ACTIONS.get(target_status, "Status Updated")
        elif not action:
            action = "Note Logged"

        activity = LeadService.record_activity(
            db,
            lead,
            action=action,
            description=remarks,
            from_status=current_status,
            to_status=target_status,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.commit()
        db.refresh(lead)
        db.refresh(activity)

        return lead, activity

    @staticmethod
    def get_junior_roles_for_user(user_role_ids: set[str], db: Session) -> set[str]:
        return HierarchyService.junior_role_ids(set(user_role_ids), db)

    @staticmethod
    def get_visible_leads(user_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> list[Lead]:
        if is_super_admin:
            return db.query(Lead).order_by(Lead.created_at.desc()).all()

        visible = HierarchyService.visible_user_ids(
            {"user_id": user_id, "is_super_admin": False}, db
        ) or {user_id}
        visible_uuids = [UUID(uid) for uid in visible]

        # A lead belongs to whoever created it and whoever it is assigned
        # to, so a manager sees both kinds for everyone in their team.
        query = db.query(Lead).filter(
            or_(
                Lead.creator_id.in_(visible_uuids),
                Lead.assigned_to_id.in_(visible_uuids),
            )
        )
        return query.order_by(Lead.created_at.desc()).all()

    @staticmethod
    def create_lead(request, creator_id: UUID, db: Session) -> Lead:
        assigned_to_uuid = UUID(request.assigned_to_id) if getattr(request, "assigned_to_id", None) else None

        title_val = getattr(request, "title", None) or getattr(request, "organization_name", None) or getattr(request, "contact_name", None) or "New Lead"

        lead = Lead(
            title=title_val,
            description=request.description,
            status=normalize_lead_status(getattr(request, "status", None)),

            contact_name=getattr(request, "contact_name", None),
            organization_name=getattr(request, "organization_name", None),
            email=getattr(request, "email", None),
            mobile_number=getattr(request, "mobile_number", None),
            website=getattr(request, "website", None),
            office_address=getattr(request, "office_address", None),
            city=getattr(request, "city", None),
            zip_code=getattr(request, "zip_code", None),
            country=getattr(request, "country", "India"),
            gst_number=getattr(request, "gst_number", None),
            pan_number=getattr(request, "pan_number", None),
            coi_number=getattr(request, "coi_number", None),
            designation=getattr(request, "designation", None),
            remarks=getattr(request, "remarks", None),


            customer_type_id=getattr(request, "customer_type_id", None),
            state_id=getattr(request, "state_id", None),
            lead_source_id=getattr(request, "lead_source_id", None),

            creator_id=creator_id,
            assigned_to_id=assigned_to_uuid
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)

        # Opens the timeline with the event that started it, so a brand new
        # lead shows real history rather than an empty panel.
        LeadService.record_activity(
            db,
            lead,
            action=LEAD_STATUS_ACTIONS[LeadStatus.NEW],
            description=getattr(request, "remarks", None),
            to_status=normalize_lead_status(lead.status),
            user_id=str(creator_id),
        )

        return lead

    @staticmethod
    def assign_lead(lead_id: str, target_user_id: str, assigner_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> Lead:
        lead = db.query(Lead).filter(Lead.id == int(lead_id)).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        is_authorized = False
        if is_super_admin:
            is_authorized = True
        elif str(lead.creator_id) == assigner_id or (lead.assigned_to_id and str(lead.assigned_to_id) == assigner_id):
            is_authorized = True
        else:
            if HierarchyService.can_see_user(
                {"user_id": assigner_id, "is_super_admin": False},
                lead.creator_id,
                db,
            ):
                is_authorized = True

        if not is_authorized:
            raise HTTPException(status_code=403, detail="Only superiors within authority scope or lead owners can reassign this lead")

        lead.assigned_to_id = UUID(target_user_id)
        lead.assigned_by_id = UUID(assigner_id)
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def progress_lead(lead_id: str, request, user_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> Lead:
        lead = db.query(Lead).filter(Lead.id == int(lead_id)).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
            
        is_authorized = False
        if is_super_admin:
            is_authorized = True
        elif str(lead.creator_id) == user_id or (lead.assigned_to_id and str(lead.assigned_to_id) == user_id):
            is_authorized = True
        else:
            if HierarchyService.can_see_user(
                {"user_id": user_id, "is_super_admin": False},
                lead.creator_id,
                db,
            ):
                is_authorized = True
                        
        if not is_authorized:
            raise HTTPException(status_code=403, detail="Only the lead creator, assigned user, and their reporting superiors can progress this lead")
            
        previous_status = normalize_lead_status(lead.status)
        moved_to = None

        lead.stage = request.stage
        if request.status is not None:
            target_status = normalize_lead_status(request.status)
            assert_transition(
                "lead",
                LEAD_TRANSITIONS,
                previous_status,
                target_status,
            )
            lead.status = target_status

            if target_status != previous_status:
                moved_to = target_status
        if request.demo_status is not None:
            lead.demo_status = request.demo_status
        if request.requirements is not None:
            lead.requirements = request.requirements
        if request.quotation_type is not None:
            lead.quotation_type = request.quotation_type
        if request.quotation_items is not None:
            lead.quotation_items = request.quotation_items

        # The row-level and drawer menus still progress leads through this
        # endpoint, so it has to leave the same trail as the Log Activity form
        # - otherwise half the status changes would be missing from history.
        if moved_to:
            LeadService.record_activity(
                db,
                lead,
                action=LEAD_STATUS_ACTIONS.get(moved_to, "Status Updated"),
                description=getattr(request, "remarks", None),
                from_status=previous_status,
                to_status=moved_to,
                user_id=user_id,
                commit=False,
            )

        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def update_lead(lead_id: str, request, user_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> Lead:
        lead = db.query(Lead).filter(Lead.id == int(lead_id)).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        is_authorized = False
        if is_super_admin:
            is_authorized = True
        elif str(lead.creator_id) == user_id or (lead.assigned_to_id and str(lead.assigned_to_id) == user_id):
            is_authorized = True
        else:
            if HierarchyService.can_see_user(
                {"user_id": user_id, "is_super_admin": False},
                lead.creator_id,
                db,
            ):
                is_authorized = True

        if not is_authorized:
            raise HTTPException(status_code=403, detail="Not authorized to edit this lead")

        # "status" is handled separately so the transition can be validated.
        fields_to_update = [
            "title", "description", "stage",
            "contact_name", "organization_name", "email", "mobile_number",
            "website", "office_address", "city", "zip_code", "country",
            "gst_number", "pan_number", "coi_number", "designation", "remarks",
            "customer_type_id", "state_id", "lead_source_id"
        ]
        for field in fields_to_update:
            val = getattr(request, field, None)
            if val is not None:
                setattr(lead, field, val)

        if getattr(request, "status", None) is not None:
            target_status = normalize_lead_status(request.status)
            assert_transition(
                "lead",
                LEAD_TRANSITIONS,
                normalize_lead_status(lead.status),
                target_status,
            )
            lead.status = target_status

        if getattr(request, "assigned_to_id", None) is not None:
            lead.assigned_to_id = UUID(request.assigned_to_id) if request.assigned_to_id else None

        db.commit()
        db.refresh(lead)
        return lead
