from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.opportunity_controller import OpportunityController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.opportunity import (
    CreateOpportunityRequest,
    UpdateOpportunityRequest,
    UpdateOpportunityStatusRequest,
)

router = APIRouter(
    prefix="/opportunities",
    tags=["Opportunities"],
)


@router.get("/")
def get_opportunities(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return OpportunityController.get_all(current_user, db)


@router.get("/{opportunity_id}")
def get_opportunity(
    opportunity_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return OpportunityController.get_by_id(opportunity_id, db)


@router.post("/")
def create_opportunity(
    request: CreateOpportunityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return OpportunityController.create(request, current_user, db)


@router.put("/{opportunity_id}")
def update_opportunity(
    opportunity_id: int,
    request: UpdateOpportunityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return OpportunityController.update(
        opportunity_id,
        request,
        current_user,
        db,
    )


@router.put("/{opportunity_id}/status")
def update_opportunity_status(
    opportunity_id: int,
    request: UpdateOpportunityStatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return OpportunityController.update_status(
        opportunity_id,
        request,
        current_user,
        db,
    )
