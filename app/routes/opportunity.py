from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.opportunity_controller import OpportunityController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.opportunity import (
    CreateOpportunityRequest,
    LogOpportunityActivityRequest,
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


@router.get("/{opportunity_id}/activities")
def get_opportunity_activities(
    opportunity_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Activity History for one opportunity, newest first.

    Its own endpoint rather than a field on the list response: the table and
    the board never show history, so loading every opportunity's timeline to
    draw them would be wasted work.
    """

    return OpportunityController.get_activities(opportunity_id, current_user, db)


@router.post("/{opportunity_id}/activities")
def log_opportunity_activity(
    opportunity_id: int,
    request: LogOpportunityActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log an activity, moving the stage when one was chosen."""

    return OpportunityController.log_activity(
        opportunity_id,
        request,
        current_user,
        db,
    )


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
