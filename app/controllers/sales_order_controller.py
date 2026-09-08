from sqlalchemy.orm import Session

from app.services.sales_order_service import (
    SalesOrderService,
    serialize_sales_order,
)


class SalesOrderController:

    @staticmethod
    def get_all(current_user: dict, db: Session):
        orders = SalesOrderService.get_visible(current_user, db)

        return {
            "success": True,
            "data": [serialize_sales_order(o) for o in orders],
        }

    @staticmethod
    def get_by_id(order_id: int, db: Session):
        order = SalesOrderService.get_by_id(order_id, db)

        return {
            "success": True,
            "data": serialize_sales_order(order),
        }

    @staticmethod
    def create(request, current_user: dict, db: Session):
        order = SalesOrderService.create(request, current_user, db)

        return {
            "success": True,
            "message": "Order created successfully.",
            "data": serialize_sales_order(order),
        }

    @staticmethod
    def update(order_id: int, request, current_user: dict, db: Session):
        order = SalesOrderService.update(order_id, request, current_user, db)

        return {
            "success": True,
            "message": "Order updated successfully.",
            "data": serialize_sales_order(order),
        }

    @staticmethod
    def update_status(order_id: int, request, current_user: dict, db: Session):
        order = SalesOrderService.update_status(
            order_id,
            request,
            current_user,
            db,
        )

        return {
            "success": True,
            "message": f"Order moved to {order.status}.",
            "data": serialize_sales_order(order),
        }

    @staticmethod
    def delete(order_id: int, current_user: dict, db: Session):
        SalesOrderService.delete(order_id, current_user, db)

        return {
            "success": True,
            "message": "Order deleted successfully.",
        }
