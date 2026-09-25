"""The desks an order passes through once it has been approved.

Accounts confirm the money, inventory confirm the stock and send it out.
Each desk has a queue of orders waiting on it, a few numbers across the
top, and one decision to make on each order: approve it onward, or reject
it with a reason and put it on hold.

Where an order has got to is shown on the sales order itself, which
everyone who can see the order already has - so there is no separate board
in here.
"""

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.fulfilment import (
    ACCOUNTS,
    DESK_STAGES,
    DESKS,
    INVENTORY,
    desk_for,
    holds_desk,
)
from app.core.workflow_status import SalesOrderStatus
from app.models.proforma_invoice import ProformaInvoice
from app.models.sales_order import SalesOrder
from app.models.user import User
logger = logging.getLogger(__name__)

def _user(current_user: dict, db: Session) -> User | None:
    try:
        return (
            db.query(User).filter(User.id == current_user.get("user_id")).first()
        )
    except Exception:  # pragma: no cover - a bad token is handled upstream
        return None


def _waiting_days(order: SalesOrder) -> int:
    """How long the order has sat where it is.

    Measured from the last time anything changed on it, which is when it
    arrived at this stage. A desk's oldest item is the one that matters.
    """

    stamp = order.updated_at or order.created_at

    if stamp is None:
        return 0

    return max(0, (datetime.utcnow() - stamp).days)


class FulfilmentDeskService:

    # ------------------------------------------------------------------
    # Who may look at what
    # ------------------------------------------------------------------
    @staticmethod
    def assert_staffs(role: str, current_user: dict, db: Session) -> None:
        """Only the desk's own people, and the super admin standing in."""

        if current_user.get("is_super_admin"):
            return

        if holds_desk(_user(current_user, db), role):
            return

        raise HTTPException(
            status_code=403,
            detail=f"This is the {role} desk. Your role does not staff it.",
        )

    @staticmethod
    def can_decide(order: SalesOrder, current_user: dict, db: Session) -> bool:
        """Whether this user is the one the order is waiting on."""

        desk = desk_for(order.status)

        if desk is None:
            return False

        return current_user.get("is_super_admin") or holds_desk(
            _user(current_user, db), desk.role
        )

    # ------------------------------------------------------------------
    # The queues
    # ------------------------------------------------------------------
    @staticmethod
    def queue(role: str, current_user: dict, db: Session) -> dict:
        """Everything sitting at one desk, with its numbers.

        A desk sees every order at its stages, not only its own reporting
        line: accounts verify the whole company's payments, and an order
        that nobody could see would simply never move.
        """

        FulfilmentDeskService.assert_staffs(role, current_user, db)

        stages = DESK_STAGES.get(role, [])

        orders = (
            db.query(SalesOrder)
            .filter(SalesOrder.status.in_(stages))
            .order_by(SalesOrder.updated_at.asc())
            .all()
        )

        rows = [
            FulfilmentDeskService._row(order, db, with_stock=role == INVENTORY)
            for order in orders
        ]

        return {
            "role": role,
            "stages": [
                {
                    "status": stage,
                    "label": DESKS[stage].queue_label,
                    "asks": DESKS[stage].asks,
                    "approves_to": DESKS[stage].approves_to,
                    "count": sum(1 for row in rows if row["status"] == stage),
                }
                for stage in stages
            ],
            "orders": rows,
            "kpis": (
                FulfilmentDeskService._accounts_kpis(rows)
                if role == ACCOUNTS
                else FulfilmentDeskService._inventory_kpis(rows, current_user, db)
            ),
        }

    @staticmethod
    def _row(order: SalesOrder, db: Session, with_stock: bool = False) -> dict:
        desk = desk_for(order.status)

        invoice = (
            db.query(ProformaInvoice)
            .filter(ProformaInvoice.sales_order_id == order.id)
            .order_by(ProformaInvoice.id.desc())
            .first()
        )

        grand_total = float(order.grand_total or 0)
        advance_percent = float(
            order.advance_percent if order.advance_percent is not None else 30
        )
        expected = round(grand_total * advance_percent / 100, 2)
        paid = float(
            invoice.amount_paid if invoice else (order.advance_received or 0)
        )

        row = {
            "id": order.id,
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "company_name": order.company_name,
            "state": order.state,
            "status": order.status,
            "status_label": desk.queue_label if desk else order.status,
            "asks": desk.asks if desk else None,
            "approves_to": desk.approves_to if desk else None,
            "grand_total": grand_total,
            "advance_percent": advance_percent,
            "advance_expected": expected,
            "advance_received": paid,
            "outstanding_balance": round(grand_total - paid, 2),
            "order_date": order.order_date.isoformat() if order.order_date else None,
            "waiting_days": _waiting_days(order),
            "items": order.items or [],
            "proforma_invoice": (
                {
                    "id": invoice.id,
                    "pi_number": invoice.pi_number,
                    "status": invoice.status,
                    "grand_total": float(invoice.grand_total or 0),
                    "amount_paid": float(invoice.amount_paid or 0),
                    "balance_due": float(invoice.balance_due or 0),
                }
                if invoice
                else None
            ),
            # Accounts should not have to open the invoice to see whether
            # the advance has actually landed.
            "advance_settled": paid + 0.5 >= expected and expected > 0,
        }

        if with_stock:
            from app.services.stock_movement_service import for_order

            row["stock"] = FulfilmentDeskService.stock_for(order, db)
            row["stock_short"] = any(line["short"] for line in row["stock"])
            # What this order has already taken off the shelf, so the desk
            # is not left guessing whether it has been counted down.
            row["stock_movements"] = for_order(order.id)

        return row

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------
    @staticmethod
    def stock_for(order: SalesOrder, db: Session) -> list[dict]:
        """What the order asks for against what is actually on the shelf.

        Matched on the line's SKU against an inventory item's serial
        number, which is how the two sides are keyed; failing that, on the
        product name. A line nothing matches is reported as unknown rather
        than as zero, because those read very differently to whoever has
        to decide.
        """

        lines: list[dict] = []

        try:
            from app.services.inventory_service import InventoryService

            items = list(InventoryService.items_col.find({}))
        except Exception:  # noqa: BLE001 - the desk still has to open
            logger.exception("Could not read inventory for order %s", order.id)
            items = []

        by_serial = {
            str(item.get("serial_number") or "").upper(): item for item in items
        }
        by_name = {str(item.get("name") or "").strip().lower(): item for item in items}

        for line in order.items or []:
            sku = str(line.get("sku") or "").strip().upper()
            name = str(line.get("product") or line.get("name") or "").strip()

            item = by_serial.get(sku) or by_name.get(name.lower())

            wanted = float(line.get("qty") or line.get("quantity") or 0)

            if item is None:
                lines.append({
                    "product": name or sku or "Unnamed line",
                    "sku": sku or None,
                    "wanted": wanted,
                    "available": None,
                    "short": False,
                    "known": False,
                })
                continue

            attributes = item.get("attributes") or {}

            available = attributes.get("instock")

            if available is None:
                available = attributes.get("stock")

            try:
                available = float(available or 0)
            except (TypeError, ValueError):
                available = 0.0

            lines.append({
                "product": item.get("name") or name,
                "sku": item.get("serial_number") or sku or None,
                "wanted": wanted,
                "available": available,
                "short": available < wanted,
                "known": True,
            })

        return lines

    # ------------------------------------------------------------------
    # The numbers
    # ------------------------------------------------------------------
    @staticmethod
    def _accounts_kpis(rows: list[dict]) -> list[dict]:
        verifying = [r for r in rows if r["status"] == SalesOrderStatus.CONFIRMED]
        closing = [r for r in rows if r["status"] == SalesOrderStatus.INSTALLED]

        oldest = max((r["waiting_days"] for r in rows), default=0)

        return [
            {
                "key": "awaiting_verification",
                "label": "Awaiting Verification",
                "value": len(verifying),
                "format": "count",
                "hint": "Orders where accounts have not yet confirmed the advance",
            },
            {
                "key": "value_pending",
                "label": "Value Pending",
                "value": round(sum(r["grand_total"] for r in verifying), 2),
                "format": "currency",
                "hint": "What those orders are worth",
            },
            {
                "key": "advance_due",
                "label": "Advance Due",
                "value": round(
                    sum(
                        max(0, r["advance_expected"] - r["advance_received"])
                        for r in verifying
                    ),
                    2,
                ),
                "format": "currency",
                "hint": "Advance still to be collected on those orders",
            },
            {
                "key": "awaiting_balance",
                "label": "Awaiting Balance",
                "value": len(closing),
                "format": "count",
                "hint": "Installed orders waiting on the final payment",
            },
            {
                "key": "balance_outstanding",
                "label": "Balance Outstanding",
                "value": round(sum(r["outstanding_balance"] for r in closing), 2),
                "format": "currency",
                "hint": "Money owed on installed orders",
            },
            {
                "key": "oldest",
                "label": "Longest Waiting",
                "value": oldest,
                "format": "days",
                "hint": "How long the oldest item has sat at this desk",
            },
        ]

    @staticmethod
    def _inventory_kpis(rows: list[dict], current_user: dict, db: Session) -> list[dict]:
        from app.services.stock_movement_service import summary

        def at(*stages):
            return [r for r in rows if r["status"] in stages]

        short = [r for r in rows if r.get("stock_short")]
        oldest = max((r["waiting_days"] for r in rows), default=0)
        shelf = summary(current_user, db)

        return [
            {
                "key": "units_in_stock",
                "label": "Units In Stock",
                "value": shelf["units"],
                "format": "count",
                "hint": f"Across {shelf['products']} products on the shelf",
            },
            {
                "key": "awaiting_stock",
                "label": "Awaiting Stock",
                "value": len(at(SalesOrderStatus.PAYMENT_VERIFIED)),
                "format": "count",
                "hint": "Paid orders waiting for inventory to confirm stock",
            },
            {
                "key": "in_procurement",
                "label": "In Procurement",
                "value": len(at(SalesOrderStatus.PROCUREMENT)),
                "format": "count",
                "hint": "Stock being picked or on order",
            },
            {
                "key": "ready",
                "label": "Ready To Dispatch",
                "value": len(at(SalesOrderStatus.READY)),
                "format": "count",
                "hint": "Picked, packed and waiting to go out",
            },
            {
                "key": "short",
                "label": "Short On Stock",
                "value": len(short),
                "format": "count",
                "tone": "warn",
                "hint": "Orders with at least one line the shelf cannot cover",
            },
            {
                "key": "out_of_stock",
                "label": "Out Of Stock",
                "value": shelf["out_of_stock"],
                "format": "count",
                "tone": "warn",
                "hint": f"Products with nothing left. Oldest item here: {oldest}d",
            },
        ]

    # ------------------------------------------------------------------
    # The decision
    # ------------------------------------------------------------------
    @staticmethod
    def decide(
        order_id: int,
        approve: bool,
        remarks: str | None,
        current_user: dict,
        db: Session,
    ) -> SalesOrder:
        """Approve an order onward, or reject it and hold it with a reason."""

        from app.services.sales_order_service import SalesOrderService

        order = SalesOrderService.get_by_id(order_id, db)
        desk = desk_for(order.status)

        if desk is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Order {order.order_number} is not waiting on any desk - "
                    f"it is {order.status.replace('_', ' ').title()}."
                ),
            )

        if not FulfilmentDeskService.can_decide(order, current_user, db):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Order {order.order_number} is with the {desk.role} desk. "
                    "Only they can move it on."
                ),
            )

        remarks = (remarks or "").strip()

        if not approve and not remarks:
            raise HTTPException(
                status_code=400,
                detail="Say why it is being rejected - it goes on hold with the reason.",
            )

        target = desk.approves_to if approve else SalesOrderStatus.ON_HOLD
        previous = order.status

        order.status = target

        SalesOrderService.record_activity(
            db,
            order,
            action=(
                desk.approval_action
                if approve
                else f"Rejected By {desk.role}"
            ),
            description=remarks or None,
            from_status=previous,
            to_status=target,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.add(order)
        db.commit()
        db.refresh(order)

        from app.services.fulfilment_notice import record_stage_change

        record_stage_change(
            order,
            db,
            previous=previous,
            actor_name=current_user.get("name") or current_user.get("email"),
            actor_id=current_user.get("user_id"),
            remarks=remarks or None,
        )

        return order
