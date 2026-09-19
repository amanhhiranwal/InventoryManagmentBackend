from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.sales_order_controller import SalesOrderController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.sales_order import (
    CreateSalesOrderRequest,
    LogSalesOrderActivityRequest,
    UpdateSalesOrderRequest,
    UpdateSalesOrderStatusRequest,
)

router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
)


@router.post("")
def create_order(
    request: CreateSalesOrderRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return SalesOrderController.create(request, current_user, db)


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
    current_user=Depends(get_current_user),
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
    current_user=Depends(get_current_user),
):
    return SalesOrderController.delete(order_id, current_user, db)
