from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.sales_order_controller import SalesOrderController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.sales_order import (
    CreateSalesOrderRequest,
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
    return SalesOrderController.get_by_id(order_id, db)


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
