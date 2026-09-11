from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.controllers.opportunity_controller import OpportunityController
from app.utils.user_names import get_user_names_helper
from app.schemas.lead import (
    CreateLeadRequest,
    UpdateLeadRequest,
    ProgressLeadRequest,
    AssignLeadRequest,
    LogLeadActivityRequest,
)
from app.schemas.opportunity import ConvertLeadRequest
from app.services.lead_service import LeadService

router = APIRouter(
    prefix="/leads",
    tags=["Leads"],
)

def serialize_activity(activity, names_map: dict[str, str] | None = None) -> dict:
    """Shape one Activity History entry for the Lead Details drawer."""

    names_map = names_map or {}
    created_by = str(activity.created_by) if activity.created_by else None

    return {
        "id": str(activity.id),
        "action": activity.action,
        "description": activity.description,
        "from_status": activity.from_status,
        "to_status": activity.to_status,
        "created_by": created_by,
        "created_by_name": names_map.get(created_by) if created_by else None,
        "created_at": activity.created_at.isoformat(),
    }


@router.post("/")
def create_lead(
    request: CreateLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    first_name = current_user.get("first_name", "")
    last_name = current_user.get("last_name", "")
    creator_name = f"{first_name} {last_name}".strip() or "User"
    
    lead = LeadService.create_lead(request, UUID(user_id), db)
    return {
        "success": True,
        "message": "Lead created successfully.",
        "data": {
            "id": lead.id,
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "contact_name": lead.contact_name,
            "organization_name": lead.organization_name,
            "email": lead.email,
            "mobile_number": lead.mobile_number,
            "website": lead.website,
            "office_address": lead.office_address,
            "city": lead.city,
            "zip_code": lead.zip_code,
            "country": lead.country,
            "gst_number": lead.gst_number,
            "pan_number": lead.pan_number,
            "coi_number": lead.coi_number,
            "designation": lead.designation,
            "remarks": lead.remarks,
            "customer_type_id": lead.customer_type_id,
            "customer_type_name": lead.customer_type.name if lead.customer_type else None,
            "state_id": lead.state_id,
            "state_name": lead.state.name if lead.state else None,
            "lead_source_id": lead.lead_source_id,
            "lead_source_name": lead.lead_source.name if lead.lead_source else None,
            "creator_id": str(lead.creator_id),
            "creator_name": creator_name,
            "assigned_to_id": str(lead.assigned_to_id) if lead.assigned_to_id else None,
            "created_at": lead.created_at.isoformat(),
        }
    }

@router.get("/")
def get_leads(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()
    
    leads = LeadService.get_visible_leads(user_id, is_super_admin, role_ids, db)
    
    # Resolve names of creators & assigned users in bulk via HTTP
    all_user_ids = set()
    for l in leads:
        if l.creator_id:
            all_user_ids.add(str(l.creator_id))
        if l.assigned_to_id:
            all_user_ids.add(str(l.assigned_to_id))
        if l.assigned_by_id:
            all_user_ids.add(str(l.assigned_by_id))
            
    names_map = get_user_names_helper(list(all_user_ids), db)
    
    return {
        "success": True,
        "data": [
            {
                "id": l.id,
                "title": l.title,
                "description": l.description,
                "status": l.status,
                "stage": l.stage,
                "demo_status": l.demo_status,
                "requirements": l.requirements,
                "quotation_type": l.quotation_type,
                "quotation_items": l.quotation_items,
                "contact_name": l.contact_name,
                "organization_name": l.organization_name,
                "email": l.email,
                "mobile_number": l.mobile_number,
                "website": l.website,
                "office_address": l.office_address,
                "city": l.city,
                "zip_code": l.zip_code,
                "country": l.country,
                "gst_number": l.gst_number,
                "pan_number": l.pan_number,
                "coi_number": l.coi_number,
                "designation": l.designation,
                "remarks": l.remarks,
                "customer_type_id": l.customer_type_id,
                "customer_type_name": l.customer_type.name if l.customer_type else None,
                "state_id": l.state_id,
                "state_name": l.state.name if l.state else None,
                "lead_source_id": l.lead_source_id,
                "lead_source_name": l.lead_source.name if l.lead_source else None,
                "creator_id": str(l.creator_id),
                "creator_name": names_map.get(str(l.creator_id), "Unknown"),
                "assigned_to_id": str(l.assigned_to_id) if l.assigned_to_id else None,
                "assigned_to_name": names_map.get(str(l.assigned_to_id), None) if l.assigned_to_id else None,
                "assigned_by_id": str(l.assigned_by_id) if l.assigned_by_id else None,
                "assigned_by_name": names_map.get(str(l.assigned_by_id), None) if l.assigned_by_id else None,
                "created_at": l.created_at.isoformat(),
            }
            for l in leads
        ]
    }

@router.get("/{lead_id}")
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Fetch a single lead. The Opportunity and Lead detail views both call
    this; previously it did not exist and callers silently fell back to
    whatever was already in memory."""

    from app.models.lead import Lead as LeadModel

    lead = db.query(LeadModel).filter(LeadModel.id == lead_id).first()

    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    LeadService.assert_can_modify_lead(lead, current_user, db)

    user_ids = [
        str(uid)
        for uid in (lead.creator_id, lead.assigned_to_id, lead.assigned_by_id)
        if uid
    ]
    names_map = get_user_names_helper(user_ids, db)

    return {
        "success": True,
        "data": {
            "id": lead.id,
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "stage": lead.stage,
            "demo_status": lead.demo_status,
            "requirements": lead.requirements,
            "quotation_type": lead.quotation_type,
            "quotation_items": lead.quotation_items,
            "contact_name": lead.contact_name,
            "organization_name": lead.organization_name,
            "email": lead.email,
            "mobile_number": lead.mobile_number,
            "website": lead.website,
            "office_address": lead.office_address,
            "city": lead.city,
            "zip_code": lead.zip_code,
            "country": lead.country,
            "gst_number": lead.gst_number,
            "pan_number": lead.pan_number,
            "coi_number": lead.coi_number,
            "designation": lead.designation,
            "remarks": lead.remarks,
            "customer_type_id": lead.customer_type_id,
            "customer_type_name": (
                lead.customer_type.name if lead.customer_type else None
            ),
            "state_id": lead.state_id,
            "state_name": lead.state.name if lead.state else None,
            "lead_source_id": lead.lead_source_id,
            "lead_source_name": (
                lead.lead_source.name if lead.lead_source else None
            ),
            "creator_id": str(lead.creator_id),
            "creator_name": names_map.get(str(lead.creator_id), "Unknown"),
            "assigned_to_id": (
                str(lead.assigned_to_id) if lead.assigned_to_id else None
            ),
            "assigned_to_name": (
                names_map.get(str(lead.assigned_to_id))
                if lead.assigned_to_id
                else None
            ),
            "created_at": lead.created_at.isoformat(),
        },
    }


@router.get("/{lead_id}/activities")
def get_lead_activities(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Activity History for one lead, newest first.

    Deliberately its own endpoint rather than a field on the list response:
    the list renders a table that never shows history, so loading every
    lead's timeline to draw it would be wasted work.
    """

    activities = LeadService.get_activities(lead_id, current_user, db)

    names_map = get_user_names_helper(
        list({str(a.created_by) for a in activities if a.created_by}),
        db,
    )

    return {
        "success": True,
        "data": [serialize_activity(a, names_map) for a in activities],
    }


@router.post("/{lead_id}/activities")
def log_lead_activity(
    lead_id: str,
    request: LogLeadActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log an activity against a lead, moving its status when one was chosen."""

    lead, activity = LeadService.log_activity(lead_id, request, current_user, db)

    names_map = get_user_names_helper(
        [str(activity.created_by)] if activity.created_by else [],
        db,
    )

    return {
        "success": True,
        "message": "Activity logged successfully.",
        "data": {
            "activity": serialize_activity(activity, names_map),
            "lead": {
                "id": lead.id,
                "status": lead.status,
                "stage": lead.stage,
            },
        },
    }


@router.put("/{lead_id}/assign")
def assign_lead(
    lead_id: str,
    request: AssignLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()
    
    lead = LeadService.assign_lead(lead_id, request.assigned_to_id, user_id, is_super_admin, role_ids, db)
    # Was get_user_names_http, which is not defined anywhere - assigning a
    # lead raised NameError instead of returning the assignee's name.
    names_map = get_user_names_helper(
        [str(lead.creator_id), str(lead.assigned_to_id)],
        db,
    )
    
    return {
        "success": True,
        "message": "Lead assigned successfully.",
        "data": {
            "id": str(lead.id),
            "title": lead.title,
            "status": lead.status,
            "stage": lead.stage,
            "creator_id": str(lead.creator_id),
            "creator_name": names_map.get(str(lead.creator_id), "Unknown"),
            "assigned_to_id": str(lead.assigned_to_id),
            "assigned_to_name": names_map.get(str(lead.assigned_to_id), "Unknown"),
        }
    }

@router.put("/{lead_id}/progress")
def progress_lead(
    lead_id: str,
    request: ProgressLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()
    
    lead = LeadService.progress_lead(lead_id, request, user_id, is_super_admin, role_ids, db)
    return {
        "success": True,
        "message": f"Lead progressed to {lead.stage} successfully.",
        "data": {
            "id": str(lead.id),
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "stage": lead.stage,
            "demo_status": lead.demo_status,
            "requirements": lead.requirements,
            "quotation_type": lead.quotation_type,
            "quotation_items": lead.quotation_items,
            "creator_id": str(lead.creator_id),
            "created_at": lead.created_at.isoformat(),
        }
    }

@router.put("/{lead_id}")
def update_lead(
    lead_id: str,
    request: UpdateLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user_id"]
    is_super_admin = current_user.get("is_super_admin", False)
    role_id = current_user.get("role_id")
    role_ids = {role_id} if role_id else set()

    lead = LeadService.update_lead(lead_id, request, user_id, is_super_admin, role_ids, db)
    return {
        "success": True,
        "message": "Lead updated successfully.",
        "data": {
            "id": lead.id,
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "stage": lead.stage,
            "contact_name": lead.contact_name,
            "organization_name": lead.organization_name,
            "email": lead.email,
            "mobile_number": lead.mobile_number,
            "website": lead.website,
            "office_address": lead.office_address,
            "city": lead.city,
            "zip_code": lead.zip_code,
            "country": lead.country,
            "gst_number": lead.gst_number,
            "pan_number": lead.pan_number,
            "coi_number": lead.coi_number,
            "designation": lead.designation,
            "remarks": lead.remarks,
            "customer_type_id": lead.customer_type_id,
            "customer_type_name": lead.customer_type.name if lead.customer_type else None,
            "state_id": lead.state_id,
            "state_name": lead.state.name if lead.state else None,
            "lead_source_id": lead.lead_source_id,
            "lead_source_name": lead.lead_source.name if lead.lead_source else None,
            "creator_id": str(lead.creator_id),
            "assigned_to_id": str(lead.assigned_to_id) if lead.assigned_to_id else None,
            "created_at": lead.created_at.isoformat(),
        }
    }


@router.post("/{lead_id}/convert")
def convert_lead_to_opportunity(
    lead_id: int,
    request: ConvertLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Promote a QUALIFIED lead into an Opportunity and mark it CONVERTED."""

    return OpportunityController.convert_lead(
        lead_id,
        request,
        current_user,
        db,
    )
