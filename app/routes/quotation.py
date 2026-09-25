from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.controllers.quotation_controller import QuotationController
from app.core.config import settings
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.quotation import (
    CreateQuotationRequest,
    LogQuotationActivityRequest,
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


@router.get("/brand")
def get_brand(
    quotation_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The company details the proposal is built from.

    The preview and the PDF read the same values, so what is shown on
    screen and what the client receives cannot drift apart.
    """

    from app.services.quotation_pdf_service import ABOUT_FALLBACK, QuotationPDFService
    from app.services.quotation_service import QuotationService

    # With a quotation named, the letterhead is that quotation's selling
    # company - one installation can run several, and the preview has to
    # show what the client will actually receive.
    quotation = (
        QuotationService.get_by_id(quotation_id, db) if quotation_id else None
    )

    company = QuotationPDFService.company(db, quotation)
    paragraphs = company["about_paragraphs"] or [ABOUT_FALLBACK]

    return {
        "success": True,
        "data": {
            **company,
            "about": paragraphs,
            "sender": QuotationPDFService.sender(db, quotation),
        },
    }


@router.get("/brand/logo")
def get_brand_logo(db: Session = Depends(get_db)):
    """The brand mark, so the preview shows the same one the PDF prints.

    Deliberately open: it is a logo on a page the browser renders with an
    <img> tag, which cannot carry an Authorization header.
    """

    from fastapi.responses import FileResponse

    from app.services.quotation_pdf_service import QuotationPDFService, _logo_path

    path = _logo_path(QuotationPDFService.company(db)["logo_path"])

    if path is None:
        raise HTTPException(status_code=404, detail="No brand logo configured.")

    return FileResponse(str(path), media_type="image/jpeg")


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
    return QuotationController.get_by_id(quotation_id, db, current_user)


@router.post("/")
def create_quotation(
    request: CreateQuotationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return QuotationController.create(request, current_user, db)


@router.get("/{quotation_id}/activities")
def get_quotation_activities(
    quotation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Activity History for the quotation detail page, newest first.

    Includes the originating opportunity's entries, so the panel reads as
    one story rather than starting when the quotation was drafted.
    """

    return QuotationController.get_activities(quotation_id, current_user, db)


@router.post("/{quotation_id}/activities")
def log_quotation_activity(
    quotation_id: int,
    request: LogQuotationActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log an activity, moving the quotation's status when one was chosen."""

    return QuotationController.log_activity(
        quotation_id,
        request,
        current_user,
        db,
    )


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


@router.get("/{quotation_id}/pdf")
def download_quotation_pdf(
    quotation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The proposal PDF - the same document the client is emailed."""

    from app.services.quotation_service import QuotationService

    quotation = QuotationService.get_by_id(quotation_id, db)
    QuotationService.assert_can_modify(quotation, current_user, db)

    return Response(
        content=QuotationService.pdf(quotation, db),
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="{QuotationService.pdf_filename(quotation)}"',
        },
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
