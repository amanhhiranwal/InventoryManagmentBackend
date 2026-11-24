"""Stock In: goods receipts (GRN) and their quality check.

Every route checks its permission, and every GRN is reached through the
warehouses the caller works in; one outside them is reported not found.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.stock import APPROVE_GRN, CREATE_GRN, POST_GRN, VIEW_GRN
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission
from app.schemas.stock import CancelRequest, GrnInspect, GrnSave
from app.services.stock.grn import GoodsReceiptService

router = APIRouter(prefix="/grn", tags=["Stock In"])


@router.get("/")
def list_grns(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_GRN)),
):
    result = GoodsReceiptService.list_receipts(
        current_user, db, company_id=company_id, warehouse_id=warehouse_id, status=status,
        search=search, page=page, size=size,
    )
    return {"success": True, **result}


@router.get("/purchase-orders")
def receivable_orders(
    warehouse_id: str,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(CREATE_GRN)),
):
    """POs of the warehouse's company with goods still to come."""

    return {"success": True, "data": GoodsReceiptService.receivable_orders(warehouse_id, current_user, db, search)}


@router.get("/dispatches")
def returnable_dispatches(
    warehouse_id: str,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(CREATE_GRN)),
):
    """Dispatches of the warehouse's company that goods can come back on."""

    return {"success": True, "data": GoodsReceiptService.returnable_dispatches(warehouse_id, current_user, db, search)}


@router.get("/{grn_id}")
def get_grn(
    grn_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_GRN)),
):
    return {
        "success": True,
        "data": GoodsReceiptService.get(grn_id, current_user, db),
        "letterhead": GoodsReceiptService.letterhead(grn_id, current_user, db),
    }


@router.post("/")
def create_grn(
    request: GrnSave,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(CREATE_GRN)),
):
    return {"success": True, "message": "GRN saved as a draft.", "data": GoodsReceiptService.create(request, current_user, db)}


@router.put("/{grn_id}")
def update_grn(
    grn_id: int,
    request: GrnSave,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(CREATE_GRN)),
):
    return {"success": True, "message": "GRN updated.", "data": GoodsReceiptService.update(grn_id, request, current_user, db)}


@router.post("/{grn_id}/submit")
def submit_grn(
    grn_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(CREATE_GRN)),
):
    return {"success": True, "message": "GRN sent in.", "data": GoodsReceiptService.submit(grn_id, current_user, db)}


@router.post("/{grn_id}/receive")
def post_receipt(
    grn_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(POST_GRN)),
):
    """Post the physical receipt: the goods enter stock in quarantine."""

    return {
        "success": True,
        "message": "Receipt posted. The goods are in quarantine awaiting quality check.",
        "data": GoodsReceiptService.post_receipt(grn_id, current_user, db),
    }


@router.post("/{grn_id}/cancel")
def cancel_grn(
    grn_id: int,
    request: CancelRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(CREATE_GRN)),
):
    return {"success": True, "message": "GRN cancelled.", "data": GoodsReceiptService.cancel(grn_id, request.reason, current_user, db)}


@router.post("/{grn_id}/inspect")
def inspect_grn(
    grn_id: int,
    request: GrnInspect,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(APPROVE_GRN)),
):
    """Accept and reject per line, and post: accepted goods go into stock."""

    return {
        "success": True,
        "message": "Quality check posted. Accepted goods are available.",
        "data": GoodsReceiptService.inspect_and_post(grn_id, request, current_user, db),
    }
