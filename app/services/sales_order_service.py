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


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_order_totals(
    items,
    discount_mode: str | None = None,
    discount_input: float | None = None,
    orc_mode: str | None = None,
    orc_input: float | None = None,
    freight_charges: float = 0.0,
    installation_lumpsum: float = 0.0,
    gst_percent: float = 18.0,
    advance_received: float = 0.0,
) -> dict:
    """Derive every money figure on the order from its line items.

    Mirrors compute_totals in quotation_service so both screens agree, and
    is always recomputed on write so the stored figures cannot drift from
    the lines - or be forged by a client posting its own totals.

        subtotal     = SUM(qty * unit price)
        discount     = SUM(line * discount%), or the summary override
        taxable      = subtotal - discount + ORC + freight + installation
        gst          = taxable * gst%
        grand total  = taxable + gst
        outstanding  = grand total - advance received
    """

    rows = _items_to_json(items) or []

    subtotal = 0.0
    discount_amount = 0.0

    for row in rows:
        quantity = _as_float(
            row.get("quantity_case")
            or row.get("qty")
            or row.get("quantity")
            or 1,
            1.0,
        )
        price = _as_float(row.get("rate") or row.get("price") or 0)
        line = quantity * price

        subtotal += line
        discount_amount += line * _as_float(row.get("discount")) / 100.0

    discount_mode = (discount_mode or "AMOUNT").upper()

    if discount_input is not None:
        typed = _as_float(discount_input)
        discount_amount = (
            subtotal * typed / 100.0 if discount_mode == "PERCENT" else typed
        )

    discount_amount = max(0.0, min(discount_amount, subtotal))

    orc_mode = (orc_mode or "AMOUNT").upper()
    orc_amount = 0.0
    orc_percent = 0.0

    if orc_input is not None:
        typed = _as_float(orc_input)

        if orc_mode == "PERCENT":
            orc_percent = typed
            orc_amount = subtotal * typed / 100.0
        else:
            orc_amount = typed
            orc_percent = orc_amount / subtotal * 100.0 if subtotal else 0.0

    freight_charges = _as_float(freight_charges)
    installation_lumpsum = _as_float(installation_lumpsum)

    taxable_amount = (
        subtotal
        - discount_amount
        + orc_amount
        + freight_charges
        + installation_lumpsum
    )

    gst_percent = _as_float(gst_percent, 18.0)
    gst_amount = taxable_amount * gst_percent / 100.0
    grand_total = taxable_amount + gst_amount

    # An advance cannot exceed the order, and the balance never goes negative.
    advance_received = max(0.0, min(_as_float(advance_received), grand_total))

    return {
        "total_amount": round(subtotal, 2),
        "discount_amount": round(discount_amount, 2),
        "discount_mode": discount_mode,
        "discount_input": discount_input,
        "orc_amount": round(orc_amount, 2),
        "orc_percent": round(orc_percent, 4),
        "orc_mode": orc_mode,
        "orc_input": orc_input,
        "freight_charges": round(freight_charges, 2),
        "installation_lumpsum": round(installation_lumpsum, 2),
        "taxable_amount": round(taxable_amount, 2),
        "gst_percent": round(gst_percent, 2),
        "gst_amount": round(gst_amount, 2),
        "grand_total": round(grand_total, 2),
        "advance_received": round(advance_received, 2),
        "outstanding_balance": round(grand_total - advance_received, 2),
    }


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
            aging_0_30=request.aging_0_30 or 0.0,
            aging_31_60=request.aging_31_60 or 0.0,
            aging_61_90=request.aging_61_90 or 0.0,
            aging_91_120=request.aging_91_120 or 0.0,
            aging_121_180=request.aging_121_180 or 0.0,
            aging_above_180=request.aging_above_180 or 0.0,
            remarks=request.remarks,
            creator_id=_to_uuid(current_user.get("user_id")),
            creator_name=creator_name,
            **compute_order_totals(
                request.items,
                discount_mode=getattr(request, "discount_mode", None),
                discount_input=getattr(request, "discount_input", None),
                orc_mode=getattr(request, "orc_mode", None),
                orc_input=getattr(request, "orc_input", None),
                freight_charges=getattr(request, "freight_charges", 0.0) or 0.0,
                installation_lumpsum=(
                    getattr(request, "installation_lumpsum", 0.0) or 0.0
                ),
                gst_percent=(
                    getattr(request, "gst_percent", None)
                    if getattr(request, "gst_percent", None) is not None
                    else 18.0
                ),
                advance_received=(
                    getattr(request, "advance_received", 0.0) or 0.0
                ),
            ),
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

        # Any change to lines or charges re-derives every figure, so the
        # stored totals always match the lines they came from.
        money_fields = (
            "items", "discount_mode", "discount_input", "orc_mode",
            "orc_input", "freight_charges", "installation_lumpsum",
            "gst_percent", "advance_received",
        )

        if any(getattr(request, field, None) is not None for field in money_fields):
            def pick(field, fallback):
                value = getattr(request, field, None)
                return fallback if value is None else value

            totals = compute_order_totals(
                order.items,
                discount_mode=pick("discount_mode", order.discount_mode),
                discount_input=pick("discount_input", order.discount_input),
                orc_mode=pick("orc_mode", order.orc_mode),
                orc_input=pick("orc_input", order.orc_input),
                freight_charges=pick("freight_charges", order.freight_charges),
                installation_lumpsum=pick(
                    "installation_lumpsum", order.installation_lumpsum
                ),
                gst_percent=pick("gst_percent", order.gst_percent or 18.0),
                advance_received=pick(
                    "advance_received", order.advance_received
                ),
            )

            for key, value in totals.items():
                setattr(order, key, value)

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
        "taxable_amount": order.taxable_amount or 0.0,
        "orc_amount": order.orc_amount or 0.0,
        "orc_percent": order.orc_percent or 0.0,
        "orc_mode": order.orc_mode or "AMOUNT",
        "orc_input": order.orc_input,
        "discount_mode": order.discount_mode or "AMOUNT",
        "discount_input": order.discount_input,
        "freight_charges": order.freight_charges or 0.0,
        "installation_lumpsum": order.installation_lumpsum or 0.0,
        "gst_percent": order.gst_percent if order.gst_percent is not None else 18.0,
        "advance_received": order.advance_received or 0.0,
        "outstanding_balance": order.outstanding_balance or 0.0,
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
