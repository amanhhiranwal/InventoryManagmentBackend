from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.core.workflow_status import (
    LEAD_TRANSITIONS,
    LeadStatus,
    assert_transition,
    normalize_lead_status,
)
from app.models.lead import Lead
from app.models.lead_activity import LeadActivity
from app.models.workflow import Workflow
from uuid import UUID
from fastapi import HTTPException
import requests
import os

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

def get_users_by_roles_helper(role_ids: list[str], db: Session = None) -> list[str]:
    if not role_ids:
        return []
    if db is not None:
        try:
            from app.models.user_role import UserRole
            role_uuids = [UUID(rid) for rid in role_ids if rid]
            user_roles = db.query(UserRole.user_id).filter(UserRole.role_id.in_(role_uuids)).all()
            if user_roles:
                return [str(ur.user_id) for ur in user_roles]
        except Exception:
            pass
    try:
        auth_host = os.getenv("AUTH_SERVICE_HOST", "auth_service")
        auth_port = os.getenv("AUTH_SERVICE_PORT", "8001")
        response = requests.get(
            f"http://{auth_host}:{auth_port}/api/v1/users/by-roles",
            params={"role_ids": role_ids},
            timeout=1
        )
        if response.status_code == 200:
            return response.json().get("user_ids", [])
    except Exception:
        pass
    return []

def get_user_roles_helper(user_id: str, db: Session = None) -> list[str]:
    if not user_id:
        return []
    if db is not None:
        try:
            from app.models.user_role import UserRole
            user_roles = db.query(UserRole.role_id).filter(UserRole.user_id == UUID(user_id)).all()
            if user_roles:
                return [str(ur.role_id) for ur in user_roles]
        except Exception:
            pass
    try:
        auth_host = os.getenv("AUTH_SERVICE_HOST", "auth_service")
        auth_port = os.getenv("AUTH_SERVICE_PORT", "8001")
        response = requests.get(
            f"http://{auth_host}:{auth_port}/api/v1/users/{user_id}/role-ids",
            timeout=1
        )
        if response.status_code == 200:
            return response.json().get("role_ids", [])
    except Exception:
        pass
    return []

def get_visible_creator_user_ids(current_user: dict, db: Session) -> list[str]:
    user_id = current_user.get("user_id")
    if not user_id:
        return []
    is_super_admin = current_user.get("is_super_admin", False)
    if is_super_admin:
        return []  # Empty list signifies unrestricted Super Admin access

    role_id = current_user.get("role_id")
    user_role_ids = {role_id} if role_id else set()

    junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
    junior_user_ids = []
    if junior_role_ids:
        junior_user_ids = get_users_by_roles_helper(list(junior_role_ids), db)

    return list(set([user_id] + junior_user_ids))


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

        if visible_ids and str(lead.creator_id) in visible_ids:
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
        workflows = db.query(Workflow).all()
        
        junior_role_ids = set()
        for wf in workflows:
            nodes_list = wf.nodes if isinstance(wf.nodes, list) else []
            edges_list = wf.edges if isinstance(wf.edges, list) else []
            
            adj = {}
            for edge in edges_list:
                src = edge.get("source")
                tgt = edge.get("target")
                if src and tgt:
                    adj.setdefault(src, []).append(tgt)
            
            start_nodes = []
            for n in nodes_list:
                role_id = n.get("data", {}).get("role_id")
                if role_id in user_role_ids:
                    start_nodes.append(n.get("id"))
            
            visited = set()
            queue = list(start_nodes)
            while queue:
                curr = queue.pop(0)
                if curr not in visited:
                    visited.add(curr)
                    for neighbor in adj.get(curr, []):
                        if neighbor not in visited:
                            queue.append(neighbor)
            
            for n in nodes_list:
                if n.get("id") in visited:
                    role_id = n.get("data", {}).get("role_id")
                    if role_id and role_id not in user_role_ids:
                        junior_role_ids.add(role_id)
                        
        return junior_role_ids

    @staticmethod
    def get_visible_leads(user_id: str, is_super_admin: bool, user_role_ids: set[str], db: Session) -> list[Lead]:
        if is_super_admin:
            return db.query(Lead).order_by(Lead.created_at.desc()).all()
            
        junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
        
        junior_user_ids = []
        if junior_role_ids:
            junior_user_ids = get_users_by_roles_helper(list(junior_role_ids), db)
            
        query = db.query(Lead).filter(
            or_(
                Lead.creator_id == UUID(user_id),
                Lead.assigned_to_id == UUID(user_id),
                Lead.creator_id.in_([UUID(uid) for uid in junior_user_ids])
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
            junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
            if junior_role_ids:
                creator_role_ids = set(get_user_roles_http(str(lead.creator_id)))
                if creator_role_ids.intersection(junior_role_ids):
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
            junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
            if junior_role_ids:
                creator_role_ids = set(get_user_roles_http(str(lead.creator_id)))
                if creator_role_ids.intersection(junior_role_ids):
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
            junior_role_ids = LeadService.get_junior_roles_for_user(user_role_ids, db)
            if junior_role_ids:
                creator_role_ids = set(get_user_roles_http(str(lead.creator_id)))
                if creator_role_ids.intersection(junior_role_ids):
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
