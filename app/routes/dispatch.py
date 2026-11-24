"""Stock out: sales order availability, reservations and dispatch orders.

Every route checks its permission, and every record is reached through
the warehouses the caller works in.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.stock import COMMIT_DISPATCH, MANAGE_DISPATCH, RESERVE_STOCK, VIEW_DISPATCH
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission
from app.schemas.stock import (
    CancelRequest,
    DispatchCreate,
    DispatchDocuments,
    PackRequest,
    PickRequest,
    PodRequest,
    ReleaseRequest,
    ReserveRequest,
)
from app.services.stock.dispatch import DispatchService, ReservationService

router = APIRouter(prefix="/dispatch", tags=["Dispatch"])


@router.get("/orders/{order_id}/availability")
def availability(
    order_id: int,
    warehouse_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_DISPATCH)),
):
    """Per sales order line: ordered, reserved, dispatched, available, short."""

    return {"success": True, "data": ReservationService.availability(order_id, warehouse_id, current_user, db)}


@router.post("/orders/{order_id}/reserve")
def reserve(
    order_id: int,
    request: ReserveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(RESERVE_STOCK)),
):
    return {"success": True, "message": "Stock reserved.", "data": ReservationService.reserve(order_id, request, current_user, db)}


@router.post("/orders/{order_id}/release")
def release(
    order_id: int,
    request: ReleaseRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(RESERVE_STOCK)),
):
    return {
        "success": True,
        "message": "Reservation released.",
        "data": ReservationService.release(order_id, request.so_line_id, request.reason, current_user, db),
    }


@router.get("/")
def list_dispatches(
    warehouse_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sales_order_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_DISPATCH)),
):
    result = DispatchService.list_orders(
        current_user, db, warehouse_id=warehouse_id, status=status, search=search,
        sales_order_id=sales_order_id, page=page, size=size,
    )
    return {"success": True, **result}


@router.get("/{do_id}")
def get_dispatch(do_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(VIEW_DISPATCH))):
    return {"success": True, "data": DispatchService.get(do_id, current_user, db)}


@router.post("/")
def create_dispatch(request: DispatchCreate, db: Session = Depends(get_db), current_user=Depends(require_permission(MANAGE_DISPATCH))):
    return {"success": True, "message": "Dispatch order created.", "data": DispatchService.create(request, current_user, db)}


@router.post("/{do_id}/pick")
def pick(do_id: int, request: PickRequest, db: Session = Depends(get_db), current_user=Depends(require_permission(MANAGE_DISPATCH))):
    return {"success": True, "message": "Picked.", "data": DispatchService.pick(do_id, request, current_user, db)}


@router.post("/{do_id}/pack")
def pack(do_id: int, request: PackRequest, db: Session = Depends(get_db), current_user=Depends(require_permission(MANAGE_DISPATCH))):
    return {"success": True, "message": "Packed.", "data": DispatchService.pack(do_id, request, current_user, db)}


@router.put("/{do_id}/documents")
def documents(do_id: int, request: DispatchDocuments, db: Session = Depends(get_db),
              current_user=Depends(require_permission(MANAGE_DISPATCH))):
    return {"success": True, "message": "Documents saved.", "data": DispatchService.documents(do_id, request, current_user, db)}


@router.post("/{do_id}/commit")
def commit(do_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(COMMIT_DISPATCH))):
    """The goods have left the warehouse: the one stock-out of a sale."""

    return {"success": True, "message": "Dispatched. Stock has gone out.", "data": DispatchService.commit(do_id, current_user, db)}


@router.post("/{do_id}/pod")
def pod(do_id: int, request: PodRequest, db: Session = Depends(get_db), current_user=Depends(require_permission(MANAGE_DISPATCH))):
    return {"success": True, "message": "Delivery recorded.", "data": DispatchService.pod(do_id, request, current_user, db)}


@router.post("/{do_id}/cancel")
def cancel(do_id: int, request: CancelRequest, db: Session = Depends(get_db), current_user=Depends(require_permission(MANAGE_DISPATCH))):
    return {"success": True, "message": "Dispatch order cancelled.", "data": DispatchService.cancel(do_id, request.reason, current_user, db)}
