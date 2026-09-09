from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.quotation_controller import QuotationController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.quotation import (
    CreateQuotationRequest,
    SendQuotationRequest,
    UpdateQuotationRequest,
    UpdateQuotationStatusRequest,
)

router = APIRouter(
    prefix="/quotations",
    tags=["Quotations"],
)


@router.get("/sender")
def get_quotation_sender(current_user=Depends(get_current_user)):
    """Identity every quotation email is sent from.

    Outbound mail always leaves through the one configured SMTP account, so
    the sender is not per-user; the Send dialog shows this and does not offer
    a choice.
    """

    from app.core.config import settings

    return {
        "success": True,
        "data": {
            "name": settings.SMTP_FROM_NAME,
            "email": settings.SMTP_FROM or settings.SMTP_USER,
            "configured": bool(settings.SMTP_HOST),
        },
    }


@router.get("/")
def get_quotations(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return QuotationController.get_all(current_user, db)


@router.get("/{quotation_id}")
def get_quotation(
    quotation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return QuotationController.get_by_id(quotation_id, db)


@router.post("/")
def create_quotation(
    request: CreateQuotationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return QuotationController.create(request, current_user, db)


@router.put("/{quotation_id}")
def update_quotation(
    quotation_id: int,
    request: UpdateQuotationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return QuotationController.update(quotation_id, request, current_user, db)


@router.put("/{quotation_id}/status")
def update_quotation_status(
    quotation_id: int,
    request: UpdateQuotationStatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return QuotationController.update_status(
        quotation_id,
        request,
        current_user,
        db,
    )


@router.post("/{quotation_id}/send")
def send_quotation(
    quotation_id: int,
    request: SendQuotationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Email the quotation to the client and move it to SENT."""

    return QuotationController.send(quotation_id, request, current_user, db)
