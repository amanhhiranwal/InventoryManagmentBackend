from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.lead import CreateLeadRequest, ProgressLeadRequest
from app.services.lead_service import LeadService
from app.repositories.rbac_repository import RBACRepository
from uuid import UUID

router = APIRouter(
    prefix="/leads",
    tags=["Leads"],
)

@router.post("/")
def create_lead(
    request: CreateLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    lead = LeadService.create_lead(request, user.id, db)
    return {
        "success": True,
        "message": "Lead created successfully.",
        "data": {
            "id": str(lead.id),
            "title": lead.title,
            "description": lead.description,
            "status": lead.status,
            "creator_id": str(lead.creator_id),
            "creator_name": f"{user.first_name} {user.last_name}",
            "created_at": lead.created_at.isoformat(),
        }
    }

@router.get("/")
def get_leads(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    leads = LeadService.get_visible_leads(user, db)
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
                "creator_name": f"{l.creator.first_name} {l.creator.last_name}" if l.creator else "Unknown",
                "created_at": l.created_at.isoformat(),
            }
            for l in leads
        ]
    }


@router.put("/{lead_id}/progress")
def progress_lead(
    lead_id: str,
    request: ProgressLeadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    lead = LeadService.progress_lead(lead_id, request, user, db)
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
