from typing import Optional

from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.approvals import (
    bands,
    FOUNDER,
    PriceType,
    approval_chain,
    describe_chain,
    discount_ceiling,
)
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_super_admin
from app.models.approval import ApprovalDocument, ApprovalStatus
from app.services.approval_service import ApprovalService, DOCUMENT_LABELS, serialize_approval
from app.services.company_profile_service import CompanyProfileService

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
def get_matrix(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The discount bands, so a form can say who will have to approve."""

    rows = []
    floor = 0.0

    for bound, role in bands(db):
        rows.append({
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
            "bands": rows,
            "founder_role": FOUNDER,
            "price_types": [
                {"value": PriceType.ECP, "label": "End Customer Price"},
                {"value": PriceType.DP, "label": "Dealer Price"},
            ],
        },
    }


class BandRequest(BaseModel):
    role: str
    #: None on the last band: past every ceiling, the founder signs.
    to_percent: Optional[float] = None


class MatrixRequest(BaseModel):
    bands: list[BandRequest]


@router.put("/matrix")
def save_matrix(
    request: MatrixRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """Set who may approve how much.

    The ceilings have to climb - a CEO who could sign less than an AVP
    would make the chain nonsense - and the last band must be open, or a
    big enough discount would have nobody to approve it.
    """

    import json

    entries = [
        {"role": band.role.strip(), "to_percent": band.to_percent}
        for band in request.bands
        if band.role.strip()
    ]

    if not entries:
        raise HTTPException(status_code=400, detail="Add at least one approval band.")

    if entries[-1]["to_percent"] is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "The last band must have no ceiling, or a discount above it "
                "would have nobody who could approve it."
            ),
        )

    previous = 0.0

    for entry in entries[:-1]:
        ceiling = entry["to_percent"]

        if ceiling is None:
            raise HTTPException(
                status_code=400,
                detail=f"Set a ceiling for the {entry['role']}.",
            )

        if ceiling <= previous:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The {entry['role']}'s ceiling must be above the "
                    f"{previous:g}% below it."
                ),
            )

        previous = ceiling

    CompanyProfileService.save_raw("discount_bands", json.dumps(entries), db)

    return {
        "success": True,
        "message": "Approval bands saved. New proposals use them straight away.",
        "data": get_matrix(db, current_user)["data"],
    }


@router.get("/preview")
def preview_chain(
    price_type: str | None = None,
    discount_percent: float = 0.0,
    customer_type: str | None = None,
    document_type: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Who would have to approve this, without raising anything.

    ``customer_type`` is what the form actually knows, and the price list
    follows from it. ``price_type`` is still accepted for anything asking
    the question directly.
    """

    from app.core.approvals import price_type_for

    if customer_type is not None:
        price_type = price_type_for(customer_type)

    price_type = price_type or PriceType.ECP

    # Who would sign depends on who is asking: a discount inside the
    # asker's own authority needs nobody. Answering without that showed a
    # chain on the form that the order would never actually raise.
    from app.models.user import User
    from app.services.approval_service import ApprovalService

    asker = (
        db.query(User)
        .filter(User.id == current_user.get("user_id"))
        .first()
        if current_user and current_user.get("user_id")
        else None
    )

    chain = ApprovalService.chain_for(
        price_type, discount_percent, asker, db, document_type
    )

    return {
        "success": True,
        "data": {
            "chain": chain,
            "price_type": price_type,
            "reason": describe_chain(
                price_type, discount_percent, db, document_type, chain
            ),
            "needs_approval": bool(chain),
            "ceilings": {
                role: discount_ceiling(role, db) for _, role in bands(db)
            },
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
            # Not always "no discount": an AVP's 8% needs nobody because
            # it is theirs to give, and the old wording told them there
            # was no discount on an order that plainly had one.
            "message": "This needs no approval - it is within your own authority.",
            "data": None,
        }

    waiting_on = approval.steps[approval.current_step]["role"]

    return {
        "success": True,
        "message": f"Sent to the {waiting_on} for approval.",
        "data": serialize_approval(approval, db),
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
            serialize_approval(a, db) for a in ApprovalService.waiting_on(current_user, db)
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
            serialize_approval(a, db)
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
        "data": serialize_approval(approval, db) if approval else None,
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
        "data": serialize_approval(approval, db),
    }


@router.get("/decide-by-link")
def decide_by_link_page(
    token: str,
    db: Session = Depends(get_db),
):
    """Direct unauthenticated email approvals are disabled.

    Redirect the user to the document in the CRM where they must be logged in.
    """
    from app.services.approval_link_service import read_token
    from app.services.approval_service import DOCUMENT_LINKS
    from app.services.company_profile_service import app_url
    from app.models.approval import SalesApproval

    claim = read_token(token)
    if claim is not None:
        approval = (
            db.query(SalesApproval)
            .filter(SalesApproval.id == claim.get("approval_id"))
            .first()
        )
        if approval:
            template = DOCUMENT_LINKS.get(approval.document_type)
            if template:
                link = template.format(id=approval.document_id)
                return RedirectResponse(
                    url=app_url(link, db),
                    status_code=303,
                )

    base = app_url(db)
    return RedirectResponse(url=f"{base}/login", status_code=303)


@router.post("/decide-by-link")
async def execute_decide_by_link(
    request: Request,
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Direct unauthenticated email decisions are refused.

    Users must log in to the CRM to review and sign off on approvals.
    """
    raise HTTPException(
        status_code=401,
        detail="Please log in to the CRM to approve or reject.",
    )


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
        "data": serialize_approval(approval, db),
    }
