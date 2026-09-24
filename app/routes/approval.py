from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.approvals import (
    DISCOUNT_BANDS,
    FOUNDER,
    PriceType,
    approval_chain,
    describe_chain,
    discount_ceiling,
)
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.models.approval import ApprovalDocument
from app.services.approval_service import ApprovalService, serialize_approval

router = APIRouter(
    prefix="/approvals",
    tags=["Approvals"],
)


class RequestApprovalRequest(BaseModel):
    document_type: str
    document_id: int
    document_number: Optional[str] = None
    price_type: str = PriceType.ECP
    discount_percent: float = 0.0
    discount_amount: Optional[float] = None
    orc_percent: Optional[float] = None
    orc_amount: Optional[float] = None
    document_value: Optional[float] = None
    remarks: Optional[str] = None


class DecideRequest(BaseModel):
    approve: bool
    remarks: Optional[str] = None


@router.get("/matrix")
def get_matrix(current_user=Depends(get_current_user)):
    """The discount bands, so a form can say who will have to approve."""

    bands = []
    floor = 0.0

    for bound, role in DISCOUNT_BANDS:
        bands.append({
            "role": role,
            "from_percent": floor,
            "to_percent": bound,
            "label": (
                f"up to {bound:g}%" if bound is not None else "any discount"
            ),
        })
        if bound is not None:
            floor = bound

    return {
        "success": True,
        "data": {
            "bands": bands,
            "founder_role": FOUNDER,
            "price_types": [
                {"value": PriceType.ECP, "label": "End Customer Price"},
                {"value": PriceType.DP, "label": "Dealer Price"},
            ],
        },
    }


@router.get("/preview")
def preview_chain(
    price_type: str = PriceType.ECP,
    discount_percent: float = 0.0,
    current_user=Depends(get_current_user),
):
    """Who would have to approve this, without raising anything."""

    chain = approval_chain(price_type, discount_percent)

    return {
        "success": True,
        "data": {
            "chain": chain,
            "reason": describe_chain(price_type, discount_percent),
            "needs_approval": bool(chain),
            "ceilings": {role: discount_ceiling(role) for _, role in DISCOUNT_BANDS},
        },
    }


@router.post("")
def request_approval(
    request: RequestApprovalRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    approval = ApprovalService.request(
        request.document_type,
        request.document_id,
        document_number=request.document_number,
        price_type=request.price_type,
        discount_percent=request.discount_percent,
        discount_amount=request.discount_amount,
        orc_percent=request.orc_percent,
        orc_amount=request.orc_amount,
        document_value=request.document_value,
        remarks=request.remarks,
        current_user=current_user,
        db=db,
    )

    if approval is None:
        return {
            "success": True,
            "message": "No discount, so this needs no approval.",
            "data": None,
        }

    waiting_on = approval.steps[approval.current_step]["role"]

    return {
        "success": True,
        "message": f"Sent to the {waiting_on} for approval.",
        "data": serialize_approval(approval),
    }


@router.get("/pending")
def pending_for_me(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """What this user is holding up."""

    return {
        "success": True,
        "data": [
            serialize_approval(a) for a in ApprovalService.waiting_on(current_user, db)
        ],
    }


@router.get("")
def list_approvals(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Every request raised by someone this user can see."""

    return {
        "success": True,
        "data": [
            serialize_approval(a)
            for a in ApprovalService.raised_by_team(current_user, db)
        ],
    }


@router.get("/document/{document_type}/{document_id}")
def approval_for_document(
    document_type: str,
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if document_type not in ApprovalDocument.ALL:
        return {"success": True, "data": None}

    approval = ApprovalService.latest_for(document_type, document_id, db)

    return {
        "success": True,
        "data": serialize_approval(approval) if approval else None,
    }


@router.put("/{approval_id}/decide")
def decide(
    approval_id: int,
    request: DecideRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    approval = ApprovalService.decide(
        approval_id,
        request.approve,
        request.remarks,
        current_user,
        db,
    )

    return {
        "success": True,
        "message": (
            "Approved."
            if request.approve
            else "Rejected, and the requester has been told."
        ),
        "data": serialize_approval(approval),
    }


@router.put("/{approval_id}/withdraw")
def withdraw(
    approval_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    approval = ApprovalService.withdraw(approval_id, current_user, db)

    return {
        "success": True,
        "message": "Approval request withdrawn.",
        "data": serialize_approval(approval),
    }
