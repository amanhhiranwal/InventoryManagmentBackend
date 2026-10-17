from typing import Optional

from fastapi.responses import HTMLResponse
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
        "message": "Approval bands saved. New quotations use them straight away.",
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
    """Show the confirmation page to decide straight from the email.

    GET only renders the confirmation page and never alters state, so
    automated email security crawlers (SafeLinks, anti-phishing bots,
    preview bots) cannot accidentally approve or reject deals.
    """

    from app.services.approval_link_service import read_token

    claim = read_token(token)

    if claim is None:
        return _link_page(
            "That link is no longer valid",
            "It may have expired, or the decision may already have been "
            "made. Open the CRM and the request will be in your queue if "
            "it is still waiting.",
            ok=False,
        )

    try:
        approval, _ = ApprovalService.validate_link(
            claim["approval_id"],
            claim["user_id"],
            claim["step"],
            db,
        )
    except HTTPException as refused:
        return _link_page("That link cannot be used", str(refused.detail), ok=False)

    decision = claim["decision"]
    action_text = "Approve" if decision == "approve" else "Reject"
    action_color = "#15803d" if decision == "approve" else "#b91c1c"
    doc_label = DOCUMENT_LABELS.get(approval.document_type, "Document")
    doc_num = approval.document_number or f"#{approval.document_id}"

    return _confirm_page(
        token=token,
        doc_label=doc_label,
        doc_num=doc_num,
        action_text=action_text,
        action_color=action_color,
        approval=approval,
    )


@router.post("/decide-by-link")
async def execute_decide_by_link(
    request: Request,
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Execute the decision once the user confirms via the web form.

    Takes the token from query param or form data and applies the decision.
    """

    from app.services.approval_link_service import read_token

    if not token:
        try:
            form = await request.form()
            token = form.get("token")
        except Exception:
            token = None

    claim = read_token(token) if token else None

    if claim is None:
        return _link_page(
            "That link is no longer valid",
            "It may have expired, or the decision may already have been "
            "made. Open the CRM and the request will be in your queue if "
            "it is still waiting.",
            ok=False,
        )

    try:
        approval = ApprovalService.decide_by_link(
            claim["approval_id"],
            claim["user_id"],
            claim["step"],
            claim["decision"] == "approve",
            db,
        )
    except HTTPException as refused:
        return _link_page("That link cannot be used", str(refused.detail), ok=False)

    decided = "approved" if claim["decision"] == "approve" else "rejected"
    waiting = (
        approval.steps[approval.current_step]["role"]
        if approval.status == ApprovalStatus.PENDING and approval.steps
        else None
    )

    after = (
        f"It now goes to the {waiting}."
        if waiting
        else "Nothing further is needed."
    )

    return _link_page(
        f"{approval.document_number or 'The request'} has been {decided}",
        after,
        ok=True,
    )


def _confirm_page(
    token: str,
    doc_label: str,
    doc_num: str,
    action_text: str,
    action_color: str,
    approval,
) -> HTMLResponse:
    """A confirmation page requiring a human click, preventing automated email scanner bots from triggering decisions."""
    from html import escape

    safe_token = escape(token)
    safe_label = escape(doc_label)
    safe_num = escape(doc_num)
    safe_action = escape(action_text)

    facts = []
    if getattr(approval, "requested_by_name", None):
        facts.append(f"<strong>Raised by:</strong> {escape(approval.requested_by_name)}")
    if getattr(approval, "document_value", None):
        facts.append(f"<strong>Value:</strong> INR {float(approval.document_value):,.2f}")
    if getattr(approval, "discount_percent", None):
        facts.append(f"<strong>Discount:</strong> {float(approval.discount_percent):g}%")

    facts_html = "<br>".join(facts)
    if facts_html:
        facts_html = f'<div style="margin:16px 0;padding:12px 16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;font-size:13px;line-height:1.6;color:#334155;">{facts_html}</div>'

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Confirm {safe_action} - {safe_label} {safe_num}</title></head>
<body style="margin:0;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif">
  <div style="max-width:520px;margin:12vh auto;padding:28px 32px;background:#fff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{action_color}">
      Synergy CRM &bull; Approval Confirmation
    </p>
    <h1 style="margin:0 0 10px;font-size:19px;color:#0f172a">Confirm {safe_action}</h1>
    <p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:#475569">
      Please confirm that you want to <strong>{safe_action.lower()}</strong> {safe_label.lower()} <strong>{safe_num}</strong>.
    </p>
    {facts_html}
    <form method="POST" action="/api/v1/approvals/decide-by-link?token={safe_token}" style="margin-top:20px;">
      <input type="hidden" name="token" value="{safe_token}">
      <button type="submit" style="background:{action_color};color:#ffffff;border:none;padding:11px 24px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,.05)">
        Confirm {safe_action}
      </button>
    </form>
  </div>
</body></html>""",
        status_code=200,
    )


def _link_page(heading: str, detail: str, ok: bool) -> HTMLResponse:
    """A plain page for a mail client to land on."""

    colour = "#15803d" if ok else "#b91c1c"

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading}</title></head>
<body style="margin:0;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif">
  <div style="max-width:520px;margin:12vh auto;padding:28px 32px;background:#fff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{colour}">
      Synergy CRM
    </p>
    <h1 style="margin:0 0 10px;font-size:19px;color:#0f172a">{heading}</h1>
    <p style="margin:0;font-size:14px;line-height:1.6;color:#475569">{detail}</p>
  </div>
</body></html>""",
        status_code=200,
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
