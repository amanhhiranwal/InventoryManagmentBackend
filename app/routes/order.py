from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.sales_order_controller import SalesOrderController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission
from app.schemas.sales_order import (
    CreateSalesOrderRequest,
    LogSalesOrderActivityRequest,
    UpdateSalesOrderRequest,
    UpdateSalesOrderStatusRequest,
)

# Creating, amending and withdrawing a record are guarded. Reading is scoped by the reporting line
# elsewhere; this is the coarser question of whether the caller works this
# part of the pipeline at all. Without it the accounts clerk and the
# warehouse - who hold no sales permissions and see no sales menu - could
# still raise a proposal or an order straight at the API.
#
# Status moves are deliberately not guarded here. Who may move a record
# from one stage to the next is a narrower question, already answered by
# the approval chain and the fulfilment desks - and answered better, since
# they know which desk the record is sitting with. Logging an activity is
# likewise open to anyone who can see the record.
router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
)


@router.post("")
def create_order(
    request: CreateSalesOrderRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("order.write")),
):
    return SalesOrderController.create(request, current_user, db)


@router.get("/next-number")
def next_order_number(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The reference a new order would take. Declared above /{order_id}
    so the path is not read as an id."""

    from app.repositories.sales_order_repository import SalesOrderRepository

    return {
        "success": True,
        "data": {"order_number": SalesOrderRepository.preview_order_number(db)},
    }


@router.get("")
def get_orders(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return SalesOrderController.get_all(current_user, db)


@router.get("/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return SalesOrderController.get_by_id(order_id, db, current_user)


@router.get("/{order_id}/activities")
def get_order_activities(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Activity History for the order detail page, newest first.

    Includes the originating opportunity's entries, so the panel reads as
    one story rather than starting at the moment the order was raised.
    """

    return SalesOrderController.get_activities(order_id, current_user, db)


@router.post("/{order_id}/activities")
def log_order_activity(
    order_id: int,
    request: LogSalesOrderActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log an activity, moving the order's status when one was chosen."""

    return SalesOrderController.log_activity(order_id, request, current_user, db)


@router.put("/{order_id}")
def update_order(
    order_id: int,
    request: UpdateSalesOrderRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("order.write")),
):
    return SalesOrderController.update(order_id, request, current_user, db)


@router.put("/{order_id}/status")
def update_order_status(
    order_id: int,
    request: UpdateSalesOrderStatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return SalesOrderController.update_status(
        order_id,
        request,
        current_user,
        db,
    )


@router.delete("/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("order.write")),
):
    return SalesOrderController.delete(order_id, current_user, db)
