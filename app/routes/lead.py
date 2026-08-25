from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import requests
import os
from uuid import UUID

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.lead import CreateLeadRequest, ProgressLeadRequest, AssignLeadRequest
from app.services.lead_service import LeadService

router = APIRouter(
    prefix="/leads",
    tags=["Leads"],
)

def get_user_names_helper(user_ids: list[str], db: Session = None) -> dict[str, str]:
    if not user_ids:
        return {}
    if db is not None:
        try:
            from app.models.user import User
            user_uuids = [UUID(uid) for uid in user_ids if uid]
            users = db.query(User).filter(User.id.in_(user_uuids)).all()
            if users:
                return {str(u.id): f"{u.first_name} {u.last_name}".strip() for u in users}
        except Exception:
            pass
    try:
        auth_host = os.getenv("AUTH_SERVICE_HOST", "auth_service")
        auth_port = os.getenv("AUTH_SERVICE_PORT", "8001")
        response = requests.get(
            f"http://{auth_host}:{auth_port}/api/v1/users/names",
            params={"user_ids": user_ids},
            timeout=1
        )
        if response.status_code == 200:
            return response.json().get("names", {})
    except Exception:
        pass
    return {}

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
            "id": str(lead.id),
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
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
                "id": str(l.id),
                "title": l.title,
                "description": l.description,
                "status": l.status,
                "stage": l.stage,
                "demo_status": l.demo_status,
                "requirements": l.requirements,
                "quotation_type": l.quotation_type,
                "quotation_items": l.quotation_items,
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
    names_map = get_user_names_http([str(lead.creator_id), str(lead.assigned_to_id)])
    
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
