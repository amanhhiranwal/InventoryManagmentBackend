"""Stock leaving the shelf when an order goes out, and coming back if it
does not.

Inventory could see what was on hand and what an order wanted, but the two
never met: dispatching ten panels left the shelf still claiming ten. This
moves the stock, and writes down every movement, so "what have I got left
after that order?" has an answer and an audit trail behind it.

Stock is issued when the order is **dispatched** - the point it physically
leaves - not when it is picked, because a picked order can still be put
back. A dispatched order that is later cancelled returns its stock.

Never allowed to break the move it is reacting to: a warehouse count that
cannot be written must not stop an order going out. The movement log is
what makes that safe - a failed deduction is visible rather than silent.
"""

import logging
from datetime import datetime

from app.core.workflow_status import SalesOrderStatus
from app.database.mongodb import sync_mongo_db

logger = logging.getLogger(__name__)

movements_col = sync_mongo_db["inventory_movements"]

#: Stock leaves on dispatch and comes back if the order is cancelled or
#: put back on hold after it had already gone.
ISSUE_AT = SalesOrderStatus.DISPATCHED
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


def _write(order, item, quantity: float, direction: str, before: float, after: float, actor) -> None:
    movements_col.insert_one({
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
        if status == ISSUE_AT:
            return _move(order, OUT, actor)

        if status in RETURN_AT and _logged(order.id, OUT) and not _logged(order.id, IN):
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

    items = list(_items().find({}))

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

        query = CompanyScopeService.mongo_filter(current_user or {}, db)
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
