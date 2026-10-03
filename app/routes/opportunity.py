from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.opportunity_controller import OpportunityController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission
from app.services.opportunity_service import OpportunityService
from app.schemas.opportunity import (
    CreateOpportunityRequest,
    LogOpportunityActivityRequest,
    UpdateOpportunityRequest,
    UpdateOpportunityStatusRequest,
)

# Creating, amending and withdrawing a record are guarded. Reading is scoped by the reporting line
# elsewhere; this is the coarser question of whether the caller works this
# part of the pipeline at all. Without it the accounts clerk and the
# warehouse - who hold no sales permissions and see no sales menu - could
# still raise a proposal or an order straight at the API.
#
# Status moves are deliberately not guarded here. Who may move a record
# from one stage to the next is a narrower question, already answered by
# the approval chain and the fulfilment desks - and answered better, since
# they know which desk the record is sitting with. Logging an activity is
# likewise open to anyone who can see the record.
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
    return OpportunityController.get_by_id(opportunity_id, db, current_user)


@router.post("/")
def create_opportunity(
    request: CreateOpportunityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("opportunity.write")),
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
    current_user=Depends(require_permission("opportunity.write")),
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


@router.delete("/{opportunity_id}")
def delete_opportunity(
    opportunity_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("opportunity.write")),
):
    """Remove an opportunity, once nothing downstream depends on it."""

    OpportunityService.delete(opportunity_id, current_user, db)

    return {
        "success": True,
        "message": "Opportunity deleted successfully.",
    }
