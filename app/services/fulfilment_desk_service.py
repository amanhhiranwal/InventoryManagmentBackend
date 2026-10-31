"""The desks an order passes through once it has been approved.

The business coordinator checks the advance and processes the order,
accounts confirm the money, production name the units and get the order
ready, and logistics send it out.
Each desk has a queue of orders waiting on it, a few numbers across the
top, and one decision to make on each order: approve it onward, or reject
it with a reason and put it on hold.

Where an order has got to is shown on the sales order itself, which
everyone who can see the order already has - so there is no separate board
in here.
"""

import logging
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.timezone import iso, now_ist
from app.core.fulfilment import (
    ACCOUNTS,
    BUSINESS_COORDINATOR,
    DESK_ROLES,
    DESK_STAGES,
    DESKS,
    DISPATCH_DOCUMENTS,
    LOGISTICS,
    desk_for,
    holds_desk,
)
from app.core.workflow_status import ProformaInvoiceStatus, SalesOrderStatus
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


def _line_qty(line: dict) -> int:
    """How many units a line is for, as the order screen counts them."""

    for key in ("quantity_case", "qty", "quantity"):
        try:
            if line.get(key) is not None:
                return int(round(float(line.get(key))))
        except (TypeError, ValueError):
            continue

    return 0


def _documents(order: SalesOrder) -> dict[str, dict]:
    """The dispatch documents on an order, by kind."""

    return {
        str(entry.get("kind")): entry
        for entry in (order.attachments or [])
        if isinstance(entry, dict) and entry.get("kind") in DISPATCH_DOCUMENTS
    }


def _latest_invoice(order: SalesOrder, db: Session):
    return (
        db.query(ProformaInvoice)
        .filter(
            ProformaInvoice.sales_order_id == order.id,
            ProformaInvoice.status != ProformaInvoiceStatus.CANCELLED,
        )
        .order_by(ProformaInvoice.id.desc())
        .first()
    )


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
    def assert_can_view(order: SalesOrder, current_user: dict, db: Session) -> None:
        """Whether this user may read the order on a desk screen.

        Reading is not moving. A desk person's queue already puts every
        order in the business at their stages in front of them, so the only
        thing this adds is being able to open one that has since moved on -
        which is when it is asked about, because that is when the customer
        rings up querying a delivery. Everyone else falls back to the
        ordinary rule: the reporting line.
        """

        if current_user.get("is_super_admin"):
            return

        user = _user(current_user, db)

        if any(holds_desk(user, role) for role in DESK_ROLES):
            return

        from app.services.sales_order_service import SalesOrderService

        SalesOrderService.assert_can_edit(order, current_user, db)

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
            # Production checks the shelf; logistics needs it too, because
            # dispatch is when the stock comes off it.
            FulfilmentDeskService._row(
                order, db, with_stock=role not in (ACCOUNTS, BUSINESS_COORDINATOR)
            )
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
                FulfilmentDeskService._coordination_kpis(rows)
                if role == BUSINESS_COORDINATOR
                else FulfilmentDeskService._accounts_kpis(rows)
                if role == ACCOUNTS
                else FulfilmentDeskService._logistics_kpis(rows, db)
                if role == LOGISTICS
                else FulfilmentDeskService._production_kpis(rows, current_user, db)
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
            "advance_received_date": iso(order.advance_received_date),
            "balance_received_date": iso(order.balance_received_date),
            "order_date": iso(order.order_date) if order.order_date else None,
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
            "payment_terms": order.payment_terms,
            # What each line is for, so production can name a serial per
            # unit against the right one.
            "lines": [
                {
                    "index": index,
                    "product": line.get("product") or line.get("name") or "Unnamed line",
                    "sku": line.get("sku"),
                    "quantity": _line_qty(line),
                }
                for index, line in enumerate(order.items or [])
            ],
            "documents": [
                {
                    "kind": kind,
                    "label": label,
                    **(_documents(order).get(kind) or {}),
                    "uploaded": kind in _documents(order),
                }
                for kind, label in DISPATCH_DOCUMENTS.items()
            ],
        }

        if with_stock:
            from app.services.stock_movement_service import for_order

            row["stock"] = FulfilmentDeskService.stock_for(order, db)
            # What this order has already taken off the shelf, so the desk
            # is not left guessing whether it has been counted down.
            row["stock_movements"] = for_order(order.id)

            # Once its units are issued the order holds them, so it cannot
            # be short: the shelf showing fewer is those very units leaving.
            directions = [m["direction"] for m in row["stock_movements"]]
            issued = bool(directions) and directions[-1] == "OUT"

            if issued:
                for line in row["stock"]:
                    line["short"] = False

            row["stock_short"] = any(line["short"] for line in row["stock"])

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

            # Issued units too: a product whose every unit has gone out is
            # still in the catalogue, with nothing left on the shelf.
            items = list(InventoryService.items_col.find({}))
        except Exception:  # noqa: BLE001 - the desk still has to open
            logger.exception("Could not read inventory for order %s", order.id)
            items = []

        # The catalogue keeps one record per serial, so a product's stock is
        # every one of its units added up - matched on the line's SKU as a
        # serial or a model number, failing that on the product name.
        groups: dict[str, list[dict]] = {}

        for item in items:
            attributes = item.get("attributes") or {}

            for key in {
                "serial:" + str(item.get("serial_number") or "").strip().upper(),
                "model:" + str(attributes.get("model_number") or "").strip().upper(),
                "name:" + str(item.get("name") or "").strip().lower(),
            }:
                if not key.endswith(":"):
                    groups.setdefault(key, []).append(item)

        for line in order.items or []:
            sku = str(line.get("sku") or "").strip().upper()
            name = str(line.get("product") or line.get("name") or "").strip()

            units = (
                groups.get(f"serial:{sku}")
                or groups.get(f"model:{sku}")
                or groups.get(f"name:{name.lower()}")
            ) if sku or name else None

            item = units[0] if units else None

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

            def held(unit: dict) -> float:
                attributes = unit.get("attributes") or {}
                value = attributes.get("instock")

                if value is None:
                    value = attributes.get("stock")

                try:
                    return float(value or 0)
                except (TypeError, ValueError):
                    return 0.0

            available = sum(held(unit) for unit in units if unit.get("issued") is not True)

            lines.append({
                "product": item.get("name") or name,
                "sku": sku or item.get("serial_number") or None,
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
    def _coordination_kpis(rows: list[dict]) -> list[dict]:
        checking = [r for r in rows if r["status"] == SalesOrderStatus.CONFIRMED]
        accepted = [r for r in rows if r["status"] == SalesOrderStatus.ADVANCE_ACCEPTED]
        oldest = max((r["waiting_days"] for r in rows), default=0)

        return [
            {
                "key": "awaiting_check",
                "label": "Awaiting Advance Check",
                "value": len(checking),
                "format": "count",
                "hint": "Approved orders waiting for the advance to be recorded",
            },
            {
                "key": "ready_to_process",
                "label": "Ready To Process",
                "value": len(accepted),
                "format": "count",
                "hint": "Advance accepted, waiting to be processed to accounts",
            },
            {
                "key": "value_waiting",
                "label": "Value Waiting",
                "value": round(sum(r["grand_total"] for r in rows), 2),
                "format": "currency",
                "hint": "What the orders at this desk are worth",
            },
            {
                "key": "advance_received",
                "label": "Advance Received",
                "value": round(sum(r["advance_received"] for r in accepted), 2),
                "format": "currency",
                "hint": "Recorded on the orders ready to process",
            },
            {
                "key": "short_of_terms",
                "label": "Short Of Terms",
                "value": sum(1 for r in accepted if not r["advance_settled"]),
                "format": "count",
                "tone": "warn",
                "hint": "Accepted with less than the payment terms ask for",
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
    def _accounts_kpis(rows: list[dict]) -> list[dict]:
        verifying = [r for r in rows if r["status"] == SalesOrderStatus.ORDER_PROCESSED]
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
    def _production_kpis(rows: list[dict], current_user: dict, db: Session) -> list[dict]:
        from app.services.stock_movement_service import issued_recently, summary

        def at(*stages):
            return [r for r in rows if r["status"] in stages]

        short = [r for r in rows if r.get("stock_short")]
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
                "hint": "Paid orders waiting for production to confirm stock",
            },
            {
                "key": "in_procurement",
                "label": "In Production",
                "value": len(at(SalesOrderStatus.PROCUREMENT)),
                "format": "count",
                "hint": "Being built, picked or on order",
            },
            {
                "key": "issued_30d",
                "label": "Units Issued",
                "value": issued_recently(),
                "format": "count",
                "hint": "Units issued by serial in the last 30 days",
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
                "hint": "Products with nothing left on the shelf",
            },
            # Six figures, one row. How long each order has waited is on
            # its own row in the queue.
        ]

    @staticmethod
    def _logistics_kpis(rows: list[dict], db: Session) -> list[dict]:
        from datetime import timedelta

        def at(*stages):
            return [r for r in rows if r["status"] in stages]

        oldest = max((r["waiting_days"] for r in rows), default=0)
        ready = at(SalesOrderStatus.READY)

        delivered = (
            db.query(SalesOrder)
            .filter(
                SalesOrder.status == SalesOrderStatus.DELIVERED,
                SalesOrder.updated_at >= datetime.utcnow() - timedelta(days=30),
            )
            .count()
        )

        return [
            {
                "key": "ready",
                "label": "Ready To Dispatch",
                "value": len(ready),
                "format": "count",
                "hint": "Picked, packed and waiting to go out",
            },
            {
                "key": "documents_pending",
                "label": "Documents Pending",
                "value": sum(
                    1 for r in ready if not all(d["uploaded"] for d in r["documents"])
                ),
                "format": "count",
                "tone": "warn",
                "hint": "Ready orders missing an invoice, insurance or docket copy",
            },
            {
                "key": "dispatched",
                "label": "Dispatched",
                "value": len(at(SalesOrderStatus.DISPATCHED)),
                "format": "count",
                "hint": "Left the warehouse, not yet with the courier's last leg",
            },
            {
                "key": "out_for_delivery",
                "label": "Out For Delivery",
                "value": len(at(SalesOrderStatus.OUT_FOR_DELIVERY)),
                "format": "count",
                "hint": "On the last leg to the client",
            },
            {
                "key": "delivered_30d",
                "label": "Delivered",
                "value": delivered,
                "format": "count",
                "hint": "Orders delivered in the last 30 days",
            },
            {
                "key": "oldest",
                "label": "Longest Waiting",
                "value": oldest,
                "format": "days",
                "hint": "How long the oldest item has sat at this desk",
            },
        ]

    # ------------------------------------------------------------------
    # The decision
    # ------------------------------------------------------------------
    @staticmethod
    def assert_advance_recorded(order: SalesOrder, db: Session) -> None:
        """Refuse to verify a payment that was never recorded.

        The desk asks accounts to confirm the advance "against the
        proforma invoice", but nothing required the invoice to exist or
        the figure on it to be anything at all - so an order could reach
        the warehouse with no money and no paperwork behind it, which is
        exactly what happened to SO-00036.

        How much is enough is still the clerk's judgement: a part payment
        they are willing to accept goes through, and the queue shows them
        it is short of what was asked. Nothing at all does not.
        """

        invoice = (
            db.query(ProformaInvoice)
            .filter(
                ProformaInvoice.sales_order_id == order.id,
                ProformaInvoice.status != ProformaInvoiceStatus.CANCELLED,
            )
            .order_by(ProformaInvoice.id.desc())
            .first()
        )

        if invoice is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Order {order.order_number} has no proforma invoice. "
                    "Raise one and record the advance against it before "
                    "verifying the payment."
                ),
            )

        if float(invoice.amount_paid or 0) <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Nothing has been received against {invoice.pi_number}. "
                    "Record what the customer has paid on the invoice, then "
                    "verify it here."
                ),
            )

    @staticmethod
    def accept_advance(order: SalesOrder, amount: float | None, current_user: dict, db: Session) -> str:
        """The coordinator's check: record what arrived, against the terms.

        Written onto the proforma invoice the way a payment typed there is,
        so the invoice, the order and the accounts desk all carry the same
        figure - accounts used to open an order and read an advance of
        nothing. Less than the terms ask for is allowed, because accepting
        a part payment is the coordinator's call; the note says so.
        """

        from app.services.proforma_invoice_service import ProformaInvoiceService

        try:
            amount = float(amount or 0)
        except (TypeError, ValueError):
            amount = 0.0

        grand_total = float(order.grand_total or 0)

        if amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Enter the advance received before accepting it.",
            )

        if amount > grand_total + 0.5:
            raise HTTPException(
                status_code=400,
                detail="The advance cannot be more than the order is worth.",
            )

        invoice = _latest_invoice(order, db)

        if invoice is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Order {order.order_number} has no proforma invoice. "
                    "Generate one from the sales order first - the advance is "
                    "recorded against it."
                ),
            )

        ProformaInvoiceService.record_advance(invoice, amount, current_user, db)
        db.refresh(order)

        percent = float(order.advance_percent if order.advance_percent is not None else 30)
        expected = round(grand_total * percent / 100, 2)

        from app.services.quotation_service import format_inr

        return (
            f"{format_inr(amount)} received against {invoice.pi_number or 'the invoice'}; "
            f"the terms ask for {percent:g}% ({format_inr(expected)})."
            + (" Accepted short of the terms." if amount + 0.5 < expected else "")
        )

    @staticmethod
    def take_serials(order: SalesOrder, serials: list | None) -> list[dict]:
        """Check every serial production named, line by line.

        One serial per unit, none twice, every one on the shelf. All the
        problems are reported together so the desk fixes them in one go.
        """

        from app.services.stock_movement_service import check_serial

        lines = order.items or []
        picks: list[dict] = []
        problems: list[str] = []
        seen: set[str] = set()

        for entry in serials or []:
            serial = str((entry or {}).get("serial") or "").strip()

            if not serial:
                continue

            try:
                index = int(entry.get("line"))
            except (TypeError, ValueError):
                index = -1

            if not 0 <= index < len(lines):
                problems.append(f"{serial} is not against a line on this order.")
                continue

            if serial.upper() in seen:
                problems.append(f"{serial} is entered more than once.")
                continue

            seen.add(serial.upper())
            picks.append({"line": index, "serial": serial})

        for index, line in enumerate(lines):
            wanted = _line_qty(line)
            named = sum(1 for pick in picks if pick["line"] == index)

            if named != wanted:
                problems.append(
                    f"{line.get('product') or 'Line ' + str(index + 1)} needs "
                    f"{wanted} serial{'s' if wanted != 1 else ''}; {named} entered."
                )

        for pick in picks:
            result = check_serial(pick["serial"], lines[pick["line"]])

            if not result["found"] or not result["in_stock"] or not result["matches_line"]:
                problems.append(result["message"])

        if problems:
            raise HTTPException(status_code=400, detail=" ".join(problems))

        return picks

    @staticmethod
    def attach_document(
        order_id: int,
        kind: str,
        file: dict,
        current_user: dict,
        db: Session,
    ) -> SalesOrder:
        """Put one dispatch document on the order, replacing any earlier one.

        Kept on the order's own attachments, so the salesperson sees the
        docket and the invoice copy on the sales order too.
        """

        from sqlalchemy.orm.attributes import flag_modified

        from app.services.sales_order_service import SalesOrderService

        if kind not in DISPATCH_DOCUMENTS:
            raise HTTPException(status_code=400, detail="That is not a dispatch document.")

        order = SalesOrderService.get_by_id(order_id, db)

        if not current_user.get("is_super_admin") and not holds_desk(
            _user(current_user, db), LOGISTICS
        ):
            raise HTTPException(
                status_code=403,
                detail="Dispatch documents are uploaded by logistics.",
            )

        if order.status not in DESK_STAGES[LOGISTICS] and order.status != SalesOrderStatus.DELIVERED:
            raise HTTPException(
                status_code=409,
                detail=f"Order {order.order_number} is not with logistics.",
            )

        key = str((file or {}).get("key") or "").strip()

        if not key:
            raise HTTPException(status_code=400, detail="Upload the file first.")

        label = DISPATCH_DOCUMENTS[kind]
        kept = [
            entry
            for entry in (order.attachments or [])
            if not (isinstance(entry, dict) and entry.get("kind") == kind)
        ]
        kept.append({
            "kind": kind,
            "label": label,
            "key": key,
            "name": file.get("name") or label,
            "size": file.get("size"),
            "type": file.get("type"),
        })

        order.attachments = kept
        flag_modified(order, "attachments")

        SalesOrderService.record_activity(
            db,
            order,
            action=f"{label} Uploaded",
            description=file.get("name") or None,
            user_id=current_user.get("user_id"),
            commit=False,
        )

        db.add(order)
        db.commit()
        db.refresh(order)

        return order

    @staticmethod
    def decide(
        order_id: int,
        approve: bool,
        remarks: str | None,
        current_user: dict,
        db: Session,
        advance_amount: float | None = None,
        serials: list | None = None,
        received_date: date | None = None,
    ) -> SalesOrder:
        """Approve an order onward, or reject it and hold it with a reason.

        Three stages ask for more than a yes: the coordinator records the
        advance, production name a serial for every unit, and logistics
        cannot dispatch without the invoice, insurance and docket copies.
        """

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

        notes: list[str] = []
        picks: list[dict] = []

        # Accounts say on which day the money came in, whether it is the
        # advance they verify or the balance that closes the order.
        if approve and desk.role == ACCOUNTS:
            if received_date is None:
                raise HTTPException(
                    status_code=400,
                    detail="Enter the date the payment was received.",
                )

            if received_date > now_ist().date():
                raise HTTPException(
                    status_code=400,
                    detail="The received date cannot be in the future.",
                )

            if order.status == SalesOrderStatus.INSTALLED:
                order.balance_received_date = received_date
            else:
                order.advance_received_date = received_date

            notes.append(f"Received on {received_date.strftime('%d %b %Y')}.")

        if approve and order.status == SalesOrderStatus.CONFIRMED:
            notes.append(
                FulfilmentDeskService.accept_advance(order, advance_amount, current_user, db)
            )

        if approve and order.status == SalesOrderStatus.ORDER_PROCESSED:
            FulfilmentDeskService.assert_advance_recorded(order, db)

            # What is left to collect once the advance is in, kept on the
            # order so every screen after this one shows the same figure.
            invoice = _latest_invoice(order, db)
            paid = float(invoice.amount_paid if invoice else order.advance_received or 0)
            order.advance_received = paid
            order.outstanding_balance = round(float(order.grand_total or 0) - paid, 2)

            from app.services.quotation_service import format_inr

            notes.append(
                f"Advance {format_inr(paid)} verified. "
                f"Balance {format_inr(order.outstanding_balance)}."
            )

        if approve and order.status == SalesOrderStatus.PROCUREMENT:
            picks = FulfilmentDeskService.take_serials(order, serials)

        if approve and order.status == SalesOrderStatus.READY:
            missing = [
                label
                for kind, label in DISPATCH_DOCUMENTS.items()
                if kind not in _documents(order)
            ]

            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Upload the {', '.join(missing)} before dispatching.",
                )

        if picks:
            from app.services.stock_movement_service import issue_serials

            try:
                issue_serials(
                    order,
                    picks,
                    current_user.get("name") or current_user.get("email"),
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error))

            notes.append(
                "Serials issued: " + ", ".join(pick["serial"] for pick in picks) + "."
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
            description=" ".join([*notes, remarks]).strip() or None,
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
