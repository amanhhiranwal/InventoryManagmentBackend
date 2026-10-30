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
from bisect import bisect_left
from datetime import datetime, timedelta

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
        # Kept on the movement itself, so who may see it does not depend on
        # the product record still being there.
        "company_id": item.get("company_id"),
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

        if item is None and quantity > 0:
            # The product is not in inventory any more - removed, or the
            # line was written against an old product list - so there is
            # nothing to count down or put back. Said, not skipped quietly.
            logger.warning(
                "Order %s line %s (%s) matches nothing in inventory; %s stock not moved %s",
                order.order_number,
                line.get("sku"),
                line.get("product") or line.get("name"),
                quantity,
                direction,
            )

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


def _key(value) -> str:
    return str(value or "").strip().lower()


def _sku_known(sku: str) -> bool:
    """Whether any record in inventory carries this SKU as its model or serial."""

    pattern = {"$regex": f"^{re.escape(sku)}$", "$options": "i"}

    return _items().count_documents(
        {"$or": [{"attributes.model_number": pattern}, {"serial_number": pattern}]},
        limit=1,
    ) > 0


def belongs_to_line(unit: dict, line: dict | None) -> bool:
    """Whether this unit is the product the order line asks for.

    A line picked from inventory carries the model number as its SKU, so
    the unit's model - or its own serial, for a product recorded as one
    record - has to be that SKU. A line whose SKU inventory does not know
    (none, or one typed before the pickers read inventory) is matched on
    the product name instead, the same way the desk's stock table is.
    """

    if not line:
        return True

    attributes = unit.get("attributes") or {}
    sku = _key(line.get("sku"))

    if sku and _sku_known(sku):
        return sku in {_key(attributes.get("model_number")), _key(unit.get("serial_number"))}

    names = {_key(line.get("product")), _key(line.get("name"))} - {""}

    return bool(names & {_key(unit.get("name")), _key(attributes.get("model_number"))})


def check_serial(serial: str, line: dict | None = None) -> dict:
    """Whether a serial can be issued against this line, as production types it.

    It has to be a unit on the shelf, in stock, and the product the line
    asks for. A serial of a different product is refused and says which
    product it is, so the desk can see what they picked up.
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
                else f"{text} is not in inventory."
            ),
        }

    held = _stock_of(unit)
    attributes = unit.get("attributes") or {}
    matches = belongs_to_line(unit, line)
    model = attributes.get("model_number")
    what = f"{unit.get('name')}" + (f" ({model})" if model else "")

    return {
        "serial": unit.get("serial_number") or text,
        "found": True,
        "in_stock": held > 0,
        "product": unit.get("name"),
        "model_number": model,
        "matches_line": matches,
        "message": (
            f"{unit.get('name')} is out of stock."
            if held <= 0
            else None
            if matches
            else f"This serial is a {what}, not {(line or {}).get('sku') or 'this product'}."
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
        index = int(pick.get("line") or 0)
        line = lines[index] if 0 <= index < len(lines) else {}

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


#: Tabs on the Stock Out page: everything, still out, or put back.
STOCK_OUT_VIEWS = ("out", "returned")

#: Set once this process has made sure the movement indexes exist.
_MOVEMENT_INDEXES = False


def _ensure_movement_indexes() -> None:
    """The indexes the Stock Out page reads by. Creating one that exists is a no-op."""

    global _MOVEMENT_INDEXES

    if _MOVEMENT_INDEXES:
        return

    movements_col.create_index([("direction", 1), ("created_at", -1)])
    movements_col.create_index([("order_id", 1), ("direction", 1)])
    _MOVEMENT_INDEXES = True


def stock_out(
    current_user: dict,
    db,
    search: str | None = None,
    view: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict:
    """Every unit that has left inventory for an order, newest first.

    Read straight from the movement records the desks already write - the
    serial, the order, the customer, where it went, who issued it and when -
    so this page shows what is stored, nothing worked out afresh. A unit
    whose order was held or cancelled afterwards came back; it is still
    listed, marked returned, because it did leave.

    The search runs in the database. The figures across the top read every
    issue, but only the fields they add up, and the put-backs are read only
    for the orders those issues belong to. Out and Returned depend on the
    put-backs, so the tabs and the page are cut here.
    """

    from app.core.timezone import iso
    from app.services.company_scope_service import CompanyScopeService

    _ensure_movement_indexes()

    base: dict = {"direction": OUT}

    # Units of a company the caller is not in are not theirs to see. A
    # super admin, and anyone unrestricted, gets an empty filter. A movement
    # carries its company; one written before it did is judged by its product.
    scope = CompanyScopeService.mongo_filter(current_user or {}, db)

    if scope:
        base["$or"] = [
            scope,
            {
                "company_id": {"$exists": False},
                "item_id": {
                    "$in": [str(item["_id"]) for item in _items().find(scope, {"_id": 1})]
                },
            },
        ]

    tallied = list(
        movements_col.find(
            base,
            {"order_id": 1, "item_id": 1, "order_number": 1, "quantity": 1, "created_at": 1},
        )
    )

    # Every put-back for these orders, oldest first, per order and unit. An
    # issue is matched to the first put-back after it, so a unit sent,
    # returned, sent again and returned again shows each return at its own
    # time.
    put_back: dict[tuple, list[datetime]] = {}

    for row in movements_col.find(
        {"direction": IN, "order_id": {"$in": list({row.get("order_id") for row in tallied})}},
        {"order_id": 1, "item_id": 1, "created_at": 1},
    ).sort("created_at", 1):
        if row.get("created_at"):
            put_back.setdefault((row.get("order_id"), row.get("item_id")), []).append(row["created_at"])

    def back(row) -> datetime | None:
        times = put_back.get((row.get("order_id"), row.get("item_id"))) or []
        sent = row.get("created_at")

        if not sent:
            return None

        index = bisect_left(times, sent)

        return times[index] if index < len(times) else None

    since = datetime.utcnow() - timedelta(days=30)
    still_out = [row for row in tallied if back(row) is None]

    summary = {
        "units_out": sum(float(row.get("quantity") or 0) for row in still_out),
        "orders": len({row.get("order_number") for row in still_out} - {None, ""}),
        "last_30_days": sum(
            float(row.get("quantity") or 0)
            for row in tallied
            if row.get("created_at") and row["created_at"] >= since
        ),
        "returned": sum(
            float(row.get("quantity") or 0) for row in tallied if back(row) is not None
        ),
    }

    listed = dict(base)

    if search and str(search).strip():
        # Escaped: a serial with a dot or a bracket in it is a serial.
        pattern = {"$regex": re.escape(str(search).strip()), "$options": "i"}
        matches = {
            "$or": [
                {field: pattern}
                for field in (
                    "serial_number", "product", "model_number", "order_number",
                    "customer_name", "place", "actor",
                )
            ]
        }
        listed = {"$and": [base, matches]}

    everything = list(movements_col.find(listed).sort("created_at", -1))

    counts = {
        "all": len(everything),
        "out": sum(1 for row in everything if back(row) is None),
        "returned": sum(1 for row in everything if back(row) is not None),
    }

    if view == "out":
        everything = [row for row in everything if back(row) is None]
    elif view == "returned":
        everything = [row for row in everything if back(row) is not None]

    page = max(1, int(page or 1))
    size = max(1, min(int(size or 20), 100))
    window = everything[(page - 1) * size: page * size]

    return {
        "rows": [
            {
                "id": str(row["_id"]),
                "serial_number": row.get("serial_number"),
                "product": row.get("product"),
                "model_number": row.get("model_number"),
                "line_product": row.get("line_product"),
                "quantity": row.get("quantity"),
                "by_serial": bool(row.get("by_serial")),
                "order_id": row.get("order_id"),
                "order_number": row.get("order_number"),
                "customer_name": row.get("customer_name"),
                "place": row.get("place"),
                "state": row.get("state"),
                "actor": row.get("actor"),
                "stock_before": row.get("stock_before"),
                "stock_after": row.get("stock_after"),
                "created_at": iso(row.get("created_at")),
                "returned": back(row) is not None,
                "returned_at": iso(back(row)),
            }
            for row in window
        ],
        "total": len(everything),
        "summary": summary,
        "counts": counts,
    }


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
