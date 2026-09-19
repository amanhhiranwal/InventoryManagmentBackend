from sqlalchemy.orm import Session

from app.services.sales_order_service import (
    SalesOrderService,
    serialize_sales_order,
)
from app.utils.user_names import get_user_names_helper


def _serialize_merged_activity(row: dict, names_map: dict[str, str]) -> dict:
    """Shape one Activity History entry for the order detail page.

    ``source`` says whether the entry belongs to the order itself or to the
    opportunity it was raised from, so the page can label the two apart.
    """

    created_by = row.get("created_by")
    created_at = row.get("created_at")

    return {
        "id": row["id"],
        "source": row["source"],
        "action": row["action"],
        "description": row.get("description"),
        "from_status": row.get("from_status"),
        "to_status": row.get("to_status"),
        "created_by": created_by,
        "created_by_name": names_map.get(created_by) if created_by else None,
        "created_at": created_at.isoformat() if created_at else None,
    }


class SalesOrderController:

    @staticmethod
    def get_all(current_user: dict, db: Session):
        orders = SalesOrderService.get_visible(current_user, db)

        return {
            "success": True,
            "data": [serialize_sales_order(o) for o in orders],
        }

    @staticmethod
    def get_by_id(order_id: int, db: Session, current_user: dict | None = None):
        order = SalesOrderService.get_by_id(order_id, db)

        # Opening a record by id follows the same hierarchy as the list.
        if current_user is not None:
            SalesOrderService.assert_can_edit(order, current_user, db)

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
    def get_activities(order_id: int, current_user: dict, db: Session):
        rows = SalesOrderService.get_activities(order_id, current_user, db)

        names_map = get_user_names_helper(
            list({row["created_by"] for row in rows if row.get("created_by")}),
            db,
        )

        return {
            "success": True,
            "data": [_serialize_merged_activity(r, names_map) for r in rows],
        }

    @staticmethod
    def log_activity(order_id: int, request, current_user: dict, db: Session):
        order, activity = SalesOrderService.log_activity(
            order_id,
            request,
            current_user,
            db,
        )

        created_by = str(activity.created_by) if activity.created_by else None

        names_map = get_user_names_helper([created_by] if created_by else [], db)

        return {
            "success": True,
            "message": "Activity logged successfully.",
            "data": {
                "activity": _serialize_merged_activity(
                    {
                        "id": f"so-{activity.id}",
                        "source": "order",
                        "action": activity.action,
                        "description": activity.description,
                        "from_status": activity.from_status,
                        "to_status": activity.to_status,
                        "created_by": created_by,
                        "created_at": activity.created_at,
                    },
                    names_map,
                ),
                "order": serialize_sales_order(order),
            },
        }

    @staticmethod
    def delete(order_id: int, current_user: dict, db: Session):
        SalesOrderService.delete(order_id, current_user, db)

        return {
            "success": True,
            "message": "Order deleted successfully.",
        }
