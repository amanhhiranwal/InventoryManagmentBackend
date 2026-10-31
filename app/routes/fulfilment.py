"""The desks an order passes through after it has been approved.

Four working screens - business coordinator, accounts, production and
logistics - each showing only the
orders waiting on that desk, gated on the permission granted in Roles &
Access. Where an order has got to is answered by the sales order's own
Order Process panel, so there is no separate board here.
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.fulfilment import (
    ACCOUNTS,
    BUSINESS_COORDINATOR,
    LOGISTICS,
    PRODUCTION,
)
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission
from app.services.fulfilment_desk_service import FulfilmentDeskService
from app.services.sales_order_service import (
    SalesOrderService,
    serialize_sales_order,
)
from app.services.stock_movement_service import for_order as stock_movements_for

router = APIRouter(
    prefix="/fulfilment",
    tags=["Fulfilment"],
)


class SerialPick(BaseModel):
    line: int
    serial: str


class DecisionRequest(BaseModel):
    """Approve the order onward, or reject it with the reason.

    ``advance_amount`` is the coordinator's figure when accepting the
    advance; ``serials`` are production's units when marking it ready.
    """

    approve: bool
    remarks: str | None = None
    advance_amount: float | None = None
    #: Accounts' day the money arrived, when verifying or settling it.
    received_date: date | None = None
    serials: list[SerialPick] | None = None


class DocumentRequest(BaseModel):
    """A file already uploaded to /attachments, and which document it is."""

    kind: str
    key: str
    name: str | None = None
    size: int | None = None
    type: str | None = None


@router.get("/coordination")
def coordination_desk(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("coordination_desk.read")),
):
    """Approved orders: advances to check, and orders to process."""

    return {
        "success": True,
        "data": FulfilmentDeskService.queue(BUSINESS_COORDINATOR, current_user, db),
    }


@router.get("/accounts")
def accounts_desk(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("payment_desk.read")),
):
    """Orders waiting on accounts: advances to verify, balances to close."""

    return {
        "success": True,
        "data": FulfilmentDeskService.queue(ACCOUNTS, current_user, db),
    }


@router.get("/production")
@router.get("/procurement", include_in_schema=False)
def production_desk(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("production_desk.read")),
):
    """Orders waiting on production, each with what the shelf can cover.

    Was the procurement desk. The old path still answers, so a link in an
    email sent before the rename does not break.
    """

    return {
        "success": True,
        "data": FulfilmentDeskService.queue(PRODUCTION, current_user, db),
    }


@router.get("/logistics")
def logistics_desk(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("logistics_desk.read")),
):
    """Orders ready to go out, and orders on their way to the client."""

    return {
        "success": True,
        "data": FulfilmentDeskService.queue(LOGISTICS, current_user, db),
    }


@router.get("/serials/check")
def check_serial(
    serial: str,
    order_id: int | None = None,
    line: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("production_desk.read")),
):
    """Whether a serial is on the shelf, checked as production type it."""

    from app.services.sales_order_service import SalesOrderService
    from app.services.stock_movement_service import check_serial as check

    order_line = None

    if order_id is not None and line is not None:
        items = SalesOrderService.get_by_id(order_id, db).items or []
        order_line = items[line] if 0 <= line < len(items) else None

    return {"success": True, "data": check(serial, order_line)}


@router.put("/orders/{order_id}/documents")
def attach_document(
    order_id: int,
    request: DocumentRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Put the invoice copy, insurance copy or docket slip on an order."""

    order = FulfilmentDeskService.attach_document(
        order_id,
        request.kind,
        request.model_dump(),
        current_user,
        db,
    )

    return {"success": True, "data": serialize_sales_order(order)}


@router.get("/orders/{order_id}")
def desk_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """One order as a desk needs to see it: the lines, the money, the
    stock behind it and everything that has happened to it so far."""

    order = SalesOrderService.get_by_id(order_id, db)

    FulfilmentDeskService.assert_can_view(order, current_user, db)

    return {
        "success": True,
        "data": {
            "order": serialize_sales_order(order),
            "stock": FulfilmentDeskService.stock_for(order, db),
            # What the order actually took off the shelf. The desk queues
            # carry this too, but an order drops out of them the moment it
            # moves on - and "what did we issue against this?" is asked
            # most often afterwards, when the customer queries a delivery.
            "stock_movements": stock_movements_for(order.id),
            "can_decide": FulfilmentDeskService.can_decide(order, current_user, db),
            "activities": SalesOrderService.get_activities(
                order_id, current_user, db, access_checked=True
            ),
        },
    }


@router.put("/orders/{order_id}/decide")
def decide(
    order_id: int,
    request: DecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The desk's call: send it on, or hold it with a reason."""

    order = FulfilmentDeskService.decide(
        order_id,
        request.approve,
        request.remarks,
        current_user,
        db,
        advance_amount=request.advance_amount,
        received_date=request.received_date,
        serials=[pick.model_dump() for pick in request.serials or []],
    )

    return {"success": True, "data": serialize_sales_order(order)}
