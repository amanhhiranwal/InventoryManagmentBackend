"""Stock leaving the shelf when an order goes out, and coming back if it
does not.

Inventory could see what was on hand and what an order wanted, but the two
never met: dispatching ten panels left the shelf still claiming ten. This
moves the stock, and writes down every movement, so "what have I got left
after that order?" has an answer and an audit trail behind it.

Stock is issued when production mark the order **ready for dispatch**, by
serial number: each unit they name is checked against the shelf, marked
issued so it drops out of the catalogue, and written down with who, when
and where it is going. An order that is cancelled or held afterwards puts
its units back.

An order that reaches ready or dispatched without serials - one moved by a
super admin, or one already on its way before serials were asked for - is
counted down by quantity, as it always was.

Never allowed to break the move it is reacting to: a warehouse count that
cannot be written must not stop an order going out. The movement log is
what makes that safe - a failed deduction is visible rather than silent.
"""

import logging
import re
from datetime import datetime

from app.core.workflow_status import SalesOrderStatus
from app.database.mongodb import sync_mongo_db

logger = logging.getLogger(__name__)

movements_col = sync_mongo_db["inventory_movements"]

#: Stock leaves when the order is ready for dispatch and comes back if the
#: order is cancelled or put back on hold after it had already gone.
ISSUE_AT = SalesOrderStatus.READY
#: Where an order with nothing issued yet is still counted down by quantity:
#: one already past ready when serials started being asked for.
ISSUE_FALLBACK = (SalesOrderStatus.READY, SalesOrderStatus.DISPATCHED)
RETURN_AT = (SalesOrderStatus.CANCELLED, SalesOrderStatus.ON_HOLD)

OUT = "OUT"
IN = "IN"


def _items():
    from app.services.inventory_service import InventoryService

    return InventoryService.items_col


def _stock_of(item) -> float:
    attributes = item.get("attributes") or {}
    value = attributes.get("instock")

    if value is None:
        value = attributes.get("stock")

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _set_stock(item, quantity: float) -> None:
    attributes = dict(item.get("attributes") or {})
    field = "instock" if "instock" in attributes or "stock" not in attributes else "stock"
    attributes[field] = quantity

    _items().update_one({"_id": item["_id"]}, {"$set": {"attributes": attributes}})


def _match(line, by_serial, by_name):
    sku = str(line.get("sku") or "").strip().upper()
    name = str(line.get("product") or line.get("name") or "").strip().lower()

    return by_serial.get(sku) or by_name.get(name)


def _wanted(line) -> float:
    try:
        return float(line.get("qty") or line.get("quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def _logged(order_id: int, direction: str) -> bool:
    """Whether this order has already moved stock this way.

    Keeps the deduction idempotent: an order dispatched, put on hold and
    dispatched again must not take the stock twice.
    """

    return movements_col.count_documents(
        {"order_id": int(order_id), "direction": direction}, limit=1
    ) > 0


def _write(order, item, quantity: float, direction: str, before: float, after: float, actor, **extra) -> None:
    movements_col.insert_one({
        **extra,
        "order_id": int(order.id),
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "item_id": str(item["_id"]),
        "serial_number": item.get("serial_number"),
        "product": item.get("name"),
        "direction": direction,
        "quantity": quantity,
        "stock_before": before,
        "stock_after": after,
        "actor": actor,
        "created_at": datetime.utcnow(),
    })


def apply_for_stage(order, previous: str, db, actor: str | None = None) -> list[dict]:
    """Move stock to match an order's new stage.

    Returns what moved, for the caller to report; an empty list means
    nothing needed doing, which is the usual case.
    """

    status = str(getattr(order, "status", "") or "")

    try:
        if status in ISSUE_FALLBACK:
            # Already issued by serial when production marked it ready,
            # in which case this finds it logged and does nothing.
            return _move(order, OUT, actor)

        if status in RETURN_AT and _logged(order.id, OUT) and not _logged(order.id, IN):
            if movements_col.count_documents(
                {"order_id": int(order.id), "direction": OUT, "by_serial": True},
                limit=1,
            ):
                return _return_serials(order, actor)

            return _move(order, IN, actor)
    except Exception:  # noqa: BLE001 - never block the order's own move
        logger.exception(
            "Could not move stock for order %s reaching %s",
            getattr(order, "id", "?"),
            status,
        )

    return []


def _move(order, direction: str, actor: str | None) -> list[dict]:
    if _logged(order.id, direction):
        return []

    items = list(_items().find({"issued": {"$ne": True}}))

    by_serial = {str(i.get("serial_number") or "").upper(): i for i in items}
    by_name = {str(i.get("name") or "").strip().lower(): i for i in items}

    moved: list[dict] = []

    for line in order.items or []:
        item = _match(line, by_serial, by_name)
        quantity = _wanted(line)

        if item is None or quantity <= 0:
            continue

        before = _stock_of(item)

        # A shelf cannot go below nothing: if the warehouse dispatched more
        # than the system knew about, the count goes to zero and the
        # movement records what was actually asked for.
        after = max(0.0, before - quantity) if direction == OUT else before + quantity

        _set_stock(item, after)
        _write(order, item, quantity, direction, before, after, actor)

        moved.append({
            "product": item.get("name"),
            "serial_number": item.get("serial_number"),
            "quantity": quantity,
            "stock_before": before,
            "stock_after": after,
            "direction": direction,
        })

    return moved


def for_order(order_id: int) -> list[dict]:
    """Every movement this order caused, oldest first."""

    return [
        {
            "product": row.get("product"),
            "serial_number": row.get("serial_number"),
            "direction": row.get("direction"),
            "quantity": row.get("quantity"),
            "stock_before": row.get("stock_before"),
            "stock_after": row.get("stock_after"),
            "actor": row.get("actor"),
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
        }
        for row in movements_col.find({"order_id": int(order_id)}).sort("_id", 1)
    ]


def summary(current_user: dict, db) -> dict:
    """What the warehouse is holding, for the procurement desk's numbers."""

    try:
        from app.services.company_scope_service import CompanyScopeService

        query = dict(CompanyScopeService.mongo_filter(current_user or {}, db))
        query["issued"] = {"$ne": True}
        items = list(_items().find(query))
    except Exception:  # noqa: BLE001 - the desk still has to open
        logger.exception("Could not summarise stock")
        return {"products": 0, "units": 0.0, "out_of_stock": 0}

    units = sum(_stock_of(item) for item in items)

    return {
        "products": len(items),
        "units": round(units, 2),
        "out_of_stock": sum(1 for item in items if _stock_of(item) <= 0),
    }


# ---------------------------------------------------------------------------
# By serial number
# ---------------------------------------------------------------------------
def _unit(serial: str):
    """The catalogue unit carrying this serial that is still on the shelf."""

    text = str(serial or "").strip()

    if not text:
        return None

    pattern = {"$regex": f"^{re.escape(text)}$", "$options": "i"}

    return _items().find_one(
        {"serial_number": pattern, "issued": {"$ne": True}},
        sort=[("attributes.instock", -1)],
    )


def _line_names(line) -> set[str]:
    return {
        str(value).strip().lower()
        for value in (line.get("sku"), line.get("product"), line.get("name"))
        if value and str(value).strip()
    }


def check_serial(serial: str, line: dict | None = None) -> dict:
    """Whether a serial can be issued, as the production desk types it.

    In stock is the rule. Belonging to the product on the line is checked
    too and reported, but only as a warning: the catalogue names and the
    order's line names were typed by different people.
    """

    text = str(serial or "").strip()
    unit = _unit(text)

    if unit is None:
        issued = (
            _items().find_one(
                {
                    "serial_number": {"$regex": f"^{re.escape(text)}$", "$options": "i"},
                    "issued": True,
                }
            )
            if text
            else None
        )

        return {
            "serial": text,
            "found": False,
            "in_stock": False,
            "message": (
                f"{text} has already been issued to {issued.get('issued_order') or 'another order'}."
                if issued
                else f"{text} is not in the catalogue."
            ),
        }

    held = _stock_of(unit)
    attributes = unit.get("attributes") or {}
    names = {
        str(value).strip().lower()
        for value in (unit.get("name"), unit.get("serial_number"), attributes.get("model_number"))
        if value and str(value).strip()
    }
    matches = True if not line else bool(names & _line_names(line))

    return {
        "serial": unit.get("serial_number") or text,
        "found": True,
        "in_stock": held > 0,
        "product": unit.get("name"),
        "model_number": attributes.get("model_number"),
        "matches_line": matches,
        "message": (
            f"{unit.get('name')} is out of stock."
            if held <= 0
            else None
            if matches
            else f"In stock, but it is a {unit.get('name')} - check it belongs on this line."
        ),
    }


def place_of(order) -> str:
    """Where the order is going, as one line, for the outgoing record."""

    address = order.shipping_address or order.billing_address or {}
    parts = (
        [str(v).strip() for v in address.values() if isinstance(v, (str, int)) and str(v).strip()]
        if isinstance(address, dict)
        else [str(address)]
    )

    return ", ".join(parts) or (order.state or "")


def issue_serials(order, picks: list[dict], actor: str | None) -> list[dict]:
    """Take each named unit off the shelf for this order.

    ``picks`` is ``[{"line": index, "serial": "..."}]``, already validated
    by the desk. A unit holding one is marked issued and leaves the
    catalogue; a bulk record holding several is counted down by one. Every
    unit gets an outgoing record: what, which order, for whom, where it is
    going, who issued it and when.
    """

    place = place_of(order)
    lines = order.items or []
    moved: list[dict] = []

    for pick in picks:
        unit = _unit(pick["serial"])

        if unit is None:
            raise ValueError(f"{pick['serial']} is no longer on the shelf.")

        before = _stock_of(unit)
        after = max(0.0, before - 1)
        now = datetime.utcnow()

        attributes = dict(unit.get("attributes") or {})
        field = "instock" if "instock" in attributes or "stock" not in attributes else "stock"
        attributes[field] = after
        changes: dict = {"attributes": attributes}

        if after <= 0:
            changes.update({
                "issued": True,
                "issued_order": order.order_number,
                "issued_order_id": int(order.id),
                "issued_at": now,
            })

        _items().update_one({"_id": unit["_id"]}, {"$set": changes})

        index = int(pick.get("line") or 0)
        line = lines[index] if 0 <= index < len(lines) else {}

        _write(
            order, unit, 1, OUT, before, after, actor,
            by_serial=True,
            line=index,
            line_product=line.get("product") or line.get("name"),
            model_number=attributes.get("model_number"),
            unit_issued=after <= 0,
            place=place,
            state=order.state,
        )

        moved.append({
            "product": unit.get("name"),
            "serial_number": unit.get("serial_number"),
            "quantity": 1,
            "stock_before": before,
            "stock_after": after,
            "direction": OUT,
        })

    return moved


def _return_serials(order, actor: str | None) -> list[dict]:
    """Put back every unit this order issued by serial."""

    moved: list[dict] = []

    for row in list(movements_col.find({"order_id": int(order.id), "direction": OUT, "by_serial": True})):
        from bson import ObjectId

        unit = _items().find_one({"_id": ObjectId(row["item_id"])})

        if unit is None:
            continue

        before = _stock_of(unit)
        after = before + 1
        attributes = dict(unit.get("attributes") or {})
        field = "instock" if "instock" in attributes or "stock" not in attributes else "stock"
        attributes[field] = after

        _items().update_one(
            {"_id": unit["_id"]},
            {
                "$set": {"attributes": attributes},
                "$unset": {"issued": "", "issued_order": "", "issued_order_id": "", "issued_at": ""},
            },
        )

        _write(
            order, unit, 1, IN, before, after, actor,
            by_serial=True, line=row.get("line"), place=row.get("place"),
        )

        moved.append({
            "product": unit.get("name"),
            "serial_number": unit.get("serial_number"),
            "quantity": 1,
            "stock_before": before,
            "stock_after": after,
            "direction": IN,
        })

    return moved


def issued_recently(days: int = 30) -> int:
    """Units issued by serial in the last few days, net of any put back."""

    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)

    try:
        out = movements_col.count_documents(
            {"direction": OUT, "by_serial": True, "created_at": {"$gte": since}}
        )
        back = movements_col.count_documents(
            {"direction": IN, "by_serial": True, "created_at": {"$gte": since}}
        )
    except Exception:  # noqa: BLE001 - the desk still has to open
        logger.exception("Could not count issued units")
        return 0

    return max(0, out - back)
