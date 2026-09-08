from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.workflow_status import (
    SALES_ORDER_TRANSITIONS,
    SalesOrderStatus,
    assert_transition,
    normalize_sales_order_status,
)
from app.models.sales_order import SalesOrder
from app.repositories.opportunity_repository import OpportunityRepository
from app.repositories.sales_order_repository import SalesOrderRepository
from app.services.lead_service import get_visible_creator_user_ids


def _to_uuid(value) -> UUID | None:
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _items_to_json(items) -> list | None:
    if items is None:
        return None
    return [
        item.dict() if hasattr(item, "dict") else dict(item)
        for item in items
    ]


class SalesOrderService:

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    def get_visible(current_user: dict, db: Session) -> list[SalesOrder]:
        visible_ids = get_visible_creator_user_ids(current_user, db)
        return SalesOrderRepository.get_visible(db, visible_ids)

    @staticmethod
    def get_by_id(order_id: int, db: Session) -> SalesOrder:
        order = SalesOrderRepository.get_by_id(db, order_id)

        if order is None:
            raise HTTPException(status_code=404, detail="Sales order not found")

        return order

    # ------------------------------------------------------------------
    # Authorisation
    # ------------------------------------------------------------------
    @staticmethod
    def assert_can_edit(
        order: SalesOrder,
        current_user: dict,
        db: Session,
    ) -> None:
        if current_user.get("is_super_admin", False):
            return

        user_id = current_user.get("user_id")

        # Orders migrated from Mongo may have no creator recorded.
        if order.creator_id is None:
            return

        if str(order.creator_id) == user_id:
            return

        visible_ids = get_visible_creator_user_ids(current_user, db)

        if visible_ids and str(order.creator_id) in visible_ids:
            return

        raise HTTPException(
            status_code=403,
            detail=(
                "Permission denied. You can only modify orders created by "
                "yourself or your subordinates."
            ),
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_opportunity(opportunity_id, db: Session) -> None:
        """Reject a link to an opportunity that does not exist."""

        if opportunity_id is None:
            return

        if OpportunityRepository.get_by_id(db, opportunity_id) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Opportunity {opportunity_id} does not exist.",
            )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    @staticmethod
    def create(request, current_user: dict, db: Session) -> SalesOrder:
        SalesOrderService._validate_opportunity(request.opportunity_id, db)

        status = normalize_sales_order_status(request.status)

        first_name = current_user.get("first_name", "")
        last_name = current_user.get("last_name", "")
        creator_name = f"{first_name} {last_name}".strip() or "User"

        order = SalesOrder(
            order_number=(
                request.order_number
                or SalesOrderRepository.next_order_number(db)
            ),
            opportunity_id=request.opportunity_id,
            status=status,
            customer_name=request.customer_name,
            company_name=request.company_name,
            customer_type=request.customer_type,
            state=request.state,
            order_date=request.order_date,
            assigned_to=request.assigned_to,
            sales_executive=request.sales_executive,
            customer_information=request.customer_information,
            billing_address=request.billing_address,
            shipping_address=request.shipping_address,
            items=_items_to_json(request.items),
            total_amount=request.total_amount or 0.0,
            discount_amount=request.discount_amount or 0.0,
            gst_amount=request.gst_amount or 0.0,
            grand_total=request.grand_total or 0.0,
            aging_0_30=request.aging_0_30 or 0.0,
            aging_31_60=request.aging_31_60 or 0.0,
            aging_61_90=request.aging_61_90 or 0.0,
            aging_91_120=request.aging_91_120 or 0.0,
            aging_121_180=request.aging_121_180 or 0.0,
            aging_above_180=request.aging_above_180 or 0.0,
            remarks=request.remarks,
            creator_id=_to_uuid(current_user.get("user_id")),
            creator_name=creator_name,
        )

        return SalesOrderRepository.create(db, order)

    @staticmethod
    def update(
        order_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> SalesOrder:
        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        if getattr(request, "opportunity_id", None) is not None:
            SalesOrderService._validate_opportunity(request.opportunity_id, db)

        simple_fields = [
            "customer_name",
            "opportunity_id",
            "company_name",
            "customer_type",
            "state",
            "order_date",
            "assigned_to",
            "sales_executive",
            "customer_information",
            "billing_address",
            "shipping_address",
            "total_amount",
            "discount_amount",
            "gst_amount",
            "grand_total",
            "aging_0_30",
            "aging_31_60",
            "aging_61_90",
            "aging_91_120",
            "aging_121_180",
            "aging_above_180",
            "remarks",
        ]

        for field in simple_fields:
            value = getattr(request, field, None)
            if value is not None:
                setattr(order, field, value)

        if getattr(request, "items", None) is not None:
            order.items = _items_to_json(request.items)

        return SalesOrderRepository.save(db, order)

    @staticmethod
    def update_status(
        order_id: int,
        request,
        current_user: dict,
        db: Session,
    ) -> SalesOrder:
        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        target = str(request.status or "").upper()

        if target not in SalesOrderStatus.ALL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid sales order status '{request.status}'. "
                    f"Expected one of: {', '.join(SalesOrderStatus.ALL)}."
                ),
            )

        assert_transition(
            "sales order",
            SALES_ORDER_TRANSITIONS,
            order.status,
            target,
        )

        order.status = target

        return SalesOrderRepository.save(db, order)

    @staticmethod
    def delete(order_id: int, current_user: dict, db: Session) -> None:
        order = SalesOrderService.get_by_id(order_id, db)

        SalesOrderService.assert_can_edit(order, current_user, db)

        SalesOrderRepository.delete(db, order)


def serialize_sales_order(order: SalesOrder) -> dict:
    """Shape a SalesOrder for the API.

    ``_id`` is emitted alongside ``id`` because the existing Sales Order UI
    keys rows off ``_id`` from the previous Mongo implementation.
    """

    return {
        "id": order.id,
        "_id": str(order.id),
        "order_number": order.order_number,
        "sales_order_id": order.order_number,
        "opportunity_id": order.opportunity_id,
        "status": order.status,
        "customer_name": order.customer_name,
        "company_name": order.company_name,
        "customer_type": order.customer_type,
        "state": order.state,
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "assigned_to": order.assigned_to,
        "sales_executive": order.sales_executive,
        "customer_information": order.customer_information,
        "billing_address": order.billing_address,
        "shipping_address": order.shipping_address,
        "items": order.items or [],
        "total_amount": order.total_amount,
        "discount_amount": order.discount_amount,
        "gst_amount": order.gst_amount,
        "grand_total": order.grand_total,
        "aging_0_30": order.aging_0_30,
        "aging_31_60": order.aging_31_60,
        "aging_61_90": order.aging_61_90,
        "aging_91_120": order.aging_91_120,
        "aging_121_180": order.aging_121_180,
        "aging_above_180": order.aging_above_180,
        "remarks": order.remarks,
        "creator_id": str(order.creator_id) if order.creator_id else None,
        "creator_name": order.creator_name,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }
