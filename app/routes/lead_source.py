from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.lead_source import CreateLeadSourceRequest
from app.services.lead_source_service import LeadSourceService

router = APIRouter(
    prefix="/lead-sources",
    tags=["Lead Sources"],
)

@router.get("")
@router.get("/")
def get_lead_sources(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    sources = LeadSourceService.get_lead_sources(db)
    return {
        "success": True,
        "data": [
            {
                "id": str(s.id),
                "name": s.name,
                "code": s.code,
                "description": s.description,
                "is_active": s.is_active,
            }
            for s in sources
        ]
    }

@router.post("/")
def create_lead_source(
    request: CreateLeadSourceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    src = LeadSourceService.create_lead_source(request, db)
    return {
        "success": True,
        "message": "Lead source created successfully.",
        "data": {
            "id": str(src.id),
            "name": src.name,
            "code": src.code,
            "description": src.description,
        }
    }
