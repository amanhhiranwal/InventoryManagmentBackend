"""The desks an order passes through after it has been approved.

Two working screens - accounts and inventory - each showing only the
orders waiting on that desk, gated on the permission granted in Roles &
Access. Where an order has got to is answered by the sales order's own
Order Process panel, so there is no separate board here.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.fulfilment import ACCOUNTS, INVENTORY
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission
from app.services.fulfilment_desk_service import FulfilmentDeskService
from app.services.sales_order_service import (
    SalesOrderService,
    serialize_sales_order,
)

router = APIRouter(
    prefix="/fulfilment",
    tags=["Fulfilment"],
)


class DecisionRequest(BaseModel):
    """Approve the order onward, or reject it with the reason."""

    approve: bool
    remarks: str | None = None


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


@router.get("/procurement")
def procurement_desk(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("procurement_desk.read")),
):
    """Orders waiting on inventory, each with what the shelf can cover."""

    return {
        "success": True,
        "data": FulfilmentDeskService.queue(INVENTORY, current_user, db),
    }


@router.get("/orders/{order_id}")
def desk_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """One order as a desk needs to see it: the lines, the money, the
    stock behind it and everything that has happened to it so far."""

    order = SalesOrderService.get_by_id(order_id, db)

    SalesOrderService.assert_can_edit(order, current_user, db)

    return {
        "success": True,
        "data": {
            "order": serialize_sales_order(order),
            "stock": FulfilmentDeskService.stock_for(order, db),
            "can_decide": FulfilmentDeskService.can_decide(order, current_user, db),
            "activities": SalesOrderService.get_activities(
                order_id, current_user, db
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
    )

    return {"success": True, "data": serialize_sales_order(order)}
