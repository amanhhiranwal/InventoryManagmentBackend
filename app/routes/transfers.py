"""Warehouse transfers and cycle counts.

Every route checks its permission, and every document is reached through
the warehouses the caller works in; one outside them is reported not found.
Viewing needs stock.read, like the rest of warehouse stock.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.stock import (
    APPROVE_COUNT,
    APPROVE_TRANSFER,
    CREATE_COUNT,
    CREATE_TRANSFER,
    RECEIVE_TRANSFER,
    SHIP_TRANSFER,
    VIEW_STOCK,
)
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission
from app.schemas.stock import (
    CancelRequest,
    CountCreate,
    CountRecord,
    TransferCreate,
    TransferReceive,
    TransferShip,
    TransferUpdate,
)
from app.services.stock.counts import StockCountService
from app.services.stock.transfers import StockTransferService

router = APIRouter(prefix="/stock", tags=["Stock Transfers & Counts"])


# ----------------------------------------------------------------------
# Transfers
# ----------------------------------------------------------------------
@router.get("/transfers")
def list_transfers(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    result = StockTransferService.list_transfers(
        current_user, db, company_id=company_id, warehouse_id=warehouse_id, status=status,
        search=search, page=page, size=size,
    )
    return {"success": True, **result}


@router.get("/transfers/destinations")
def transfer_destinations(from_warehouse_id: str, db: Session = Depends(get_db),
                          current_user=Depends(require_permission(CREATE_TRANSFER))):
    """Where goods from this warehouse can go: its company's other warehouses."""

    return {"success": True, "data": StockTransferService.destinations(from_warehouse_id, current_user, db)}


@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(VIEW_STOCK))):
    return {"success": True, "data": StockTransferService.get(transfer_id, current_user, db)}


@router.post("/transfers")
def create_transfer(request: TransferCreate, db: Session = Depends(get_db),
                    current_user=Depends(require_permission(CREATE_TRANSFER))):
    return {"success": True, "message": "Transfer saved as a draft.", "data": StockTransferService.create(request, current_user, db)}


@router.put("/transfers/{transfer_id}")
def update_transfer(transfer_id: int, request: TransferUpdate, db: Session = Depends(get_db),
                    current_user=Depends(require_permission(CREATE_TRANSFER))):
    return {"success": True, "message": "Draft updated.", "data": StockTransferService.update(transfer_id, request, current_user, db)}


@router.delete("/transfers/{transfer_id}")
def delete_transfer(transfer_id: int, db: Session = Depends(get_db),
                    current_user=Depends(require_permission(CREATE_TRANSFER))):
    StockTransferService.delete(transfer_id, current_user, db)
    return {"success": True, "message": "Draft deleted."}


@router.post("/transfers/{transfer_id}/submit")
def submit_transfer(transfer_id: int, db: Session = Depends(get_db),
                    current_user=Depends(require_permission(CREATE_TRANSFER))):
    return {"success": True, "message": "Sent for approval.", "data": StockTransferService.submit(transfer_id, current_user, db)}


@router.post("/transfers/{transfer_id}/approve")
def approve_transfer(transfer_id: int, db: Session = Depends(get_db),
                     current_user=Depends(require_permission(APPROVE_TRANSFER))):
    return {"success": True, "message": "Approved.", "data": StockTransferService.approve(transfer_id, current_user, db)}


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: int, request: CancelRequest, db: Session = Depends(get_db),
                    current_user=Depends(get_current_user)):
    """The requester's side cancels until it ships; an approver declines one
    awaiting approval. Both checked in the service."""
    return {"success": True, "message": "Cancelled.", "data": StockTransferService.cancel(transfer_id, request.reason, current_user, db)}


@router.post("/transfers/{transfer_id}/ship")
def ship_transfer(transfer_id: int, request: TransferShip, db: Session = Depends(get_db),
                  current_user=Depends(require_permission(SHIP_TRANSFER))):
    return {"success": True, "message": "Shipped; the goods are in transit.", "data": StockTransferService.ship(transfer_id, request, current_user, db)}


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(transfer_id: int, request: TransferReceive, db: Session = Depends(get_db),
                     current_user=Depends(require_permission(RECEIVE_TRANSFER))):
    return {"success": True, "message": "Received.", "data": StockTransferService.receive(transfer_id, request, current_user, db)}


# ----------------------------------------------------------------------
# Cycle counts
# ----------------------------------------------------------------------
@router.get("/counts")
def list_counts(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    result = StockCountService.list_counts(
        current_user, db, company_id=company_id, warehouse_id=warehouse_id, status=status,
        search=search, page=page, size=size,
    )
    return {"success": True, **result}


@router.get("/counts/{count_id}")
def get_count(count_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(VIEW_STOCK))):
    return {"success": True, "data": StockCountService.get(count_id, current_user, db)}


@router.post("/counts")
def create_count(request: CountCreate, db: Session = Depends(get_db), current_user=Depends(require_permission(CREATE_COUNT))):
    return {"success": True, "message": "Count started.", "data": StockCountService.create(request, current_user, db)}


@router.put("/counts/{count_id}")
def record_count(count_id: int, request: CountRecord, db: Session = Depends(get_db),
                 current_user=Depends(require_permission(CREATE_COUNT))):
    return {"success": True, "message": "Counts saved.", "data": StockCountService.record(count_id, request, current_user, db)}


@router.post("/counts/{count_id}/submit")
def submit_count(count_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(CREATE_COUNT))):
    return {"success": True, "message": "Sent for approval.", "data": StockCountService.submit(count_id, current_user, db)}


@router.post("/counts/{count_id}/cancel")
def cancel_count(count_id: int, request: CancelRequest, db: Session = Depends(get_db),
                 current_user=Depends(require_permission(CREATE_COUNT))):
    return {"success": True, "message": "Cancelled.", "data": StockCountService.cancel(count_id, request.reason, current_user, db)}


@router.post("/counts/{count_id}/approve")
def approve_count(count_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(APPROVE_COUNT))):
    return {"success": True, "message": "Approved; differences posted to stock.", "data": StockCountService.approve(count_id, current_user, db)}
