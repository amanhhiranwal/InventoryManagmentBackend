"""Keeping the Inventory list and the store ledger in step until switch-over.

While the stock ledger is not live (AppSetting stock_ledger_live off), the
desks still sell from the Inventory list (MongoDB), and stock by store is
in the ledger. Products added on the Inventory form or upload go into both
already. This covers the rest, so the two books agree:

  * Production naming a serial: it is looked up in the ledger, which says
    which store holds it and refuses one that is not on the shelf there;
  * issuing it to the order takes it out of that store (ORDER_ISSUE), and
    putting the order on hold or cancelling it puts it back;
  * goods accepted on a GRN are added to the Inventory list, so they can
    be sold the same day.

Once the ledger is live none of this runs: Stock Out dispatch is then the
only stock-out, and the Inventory list is only the catalogue.
"""

import re
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.stock import (
    REVERSAL,
    SALES_DISPATCH,
    SERIAL_DISPATCHED,
    SERIAL_IN_STOCK,
    UNRESTRICTED,
)
from app.core.timezone import now_ist
from app.models.location import Location
from app.models.stock import StockLedger, StockSerial
from app.services.stock import posting

ISSUE_SOURCE = "ORDER_ISSUE"

SERIAL_LABEL = {
    "IN_STOCK": "on the shelf",
    "QUARANTINE": "in quality check",
    "REJECTED": "rejected",
    "RESERVED": "reserved",
    "PICKED": "picked for a dispatch",
    "DISPATCHED": "already dispatched",
    "IN_TRANSIT": "in transit between stores",
}


def ledger_off(db: Session) -> bool:
    from app.services.stock.dispatch import ledger_live

    return not ledger_live(db)


def serial_place(db: Session, serial: str, company_id=None) -> dict | None:
    """Where the ledger has this serial: its store and status. None when the
    ledger does not track it (shared stock, or not migrated)."""

    text = str(serial or "").strip().upper()
    if not text:
        return None
    query = db.query(StockSerial).filter(StockSerial.serial_no == text)
    if company_id:
        try:
            query = query.filter(StockSerial.company_id == uuid.UUID(str(company_id)))
        except ValueError:
            return None
    rows = query.all()
    if not rows:
        return None
    # One per company; prefer the one on the shelf.
    row = next((r for r in rows if r.status == SERIAL_IN_STOCK), rows[0])
    store = db.query(Location.location_name).filter(Location.id == row.warehouse_id).scalar()
    return {
        "status": row.status,
        "status_label": SERIAL_LABEL.get(row.status, row.status.lower()),
        "warehouse_id": str(row.warehouse_id),
        "store": store or "a store",
        "company_id": str(row.company_id),
        "sku": row.sku,
        "bin_id": row.bin_id,
        "row": row,
    }


def take_out_of_store(order, serials: list[str], company_ids: dict[str, str | None], db: Session) -> list[str]:
    """Issue these serials out of the store the ledger holds them in.

    Does not commit. Serials the ledger does not track are left alone (they
    were never put into a store). Returns the serials taken out.
    """

    groups: dict[tuple, list[str]] = {}
    for serial in serials:
        place = serial_place(db, serial, company_ids.get(serial))
        if place is None:
            continue
        if place["status"] != SERIAL_IN_STOCK:
            raise posting.StockPostingError(
                f"{serial} is {place['status_label']} at {place['store']}, not on the shelf."
            )
        key = (place["company_id"], place["warehouse_id"], str(place["bin_id"]), place["sku"])
        groups.setdefault(key, []).append(place["row"].serial_no)

    if not groups:
        return []

    movements = [
        posting.Movement(
            company_id=uuid.UUID(company_id), warehouse_id=uuid.UUID(warehouse_id), bin_id=uuid.UUID(bin_id), sku=sku,
            stock_status=UNRESTRICTED, quantity=-Decimal(len(found)), movement_type=SALES_DISPATCH,
            source_line_id=str(n), serials=found, serial_status_after=SERIAL_DISPATCHED,
        )
        for n, ((company_id, warehouse_id, bin_id, sku), found) in enumerate(groups.items(), start=1)
    ]
    posting.post(db, posting.Posting(
        source_doc_type=ISSUE_SOURCE,
        # An order held and issued again is a second issue, with its own keys.
        source_doc_id=f"{order.id}-{uuid.uuid4().hex[:8]}",
        source_doc_number=order.order_number,
        movement_date=now_ist().date(),
        remarks=f"Issued by serial to {order.order_number or order.id} at Production",
        movements=movements,
    ))
    return [s for found in groups.values() for s in found]


def put_back_in_store(order, serials: list[str], db: Session) -> list[str]:
    """Put serials this order issued back into the store they left. Does not commit."""

    movements = []
    back = []
    for serial in serials:
        text = str(serial or "").strip().upper()
        last = (
            db.query(StockLedger)
            .filter(StockLedger.source_doc_type == ISSUE_SOURCE, StockLedger.source_doc_id.like(f"{order.id}-%"),
                    StockLedger.serial_no == text)
            .order_by(StockLedger.id.desc())
            .first()
        )
        if last is None:
            continue
        unit = db.query(StockSerial).filter(StockSerial.serial_no == text, StockSerial.company_id == last.company_id,
                                            StockSerial.status == SERIAL_DISPATCHED).first()
        if unit is None:
            continue
        movements.append(posting.Movement(
            company_id=last.company_id, warehouse_id=last.warehouse_id, bin_id=last.bin_id, sku=last.sku,
            stock_status=UNRESTRICTED, quantity=Decimal(1), movement_type=REVERSAL,
            source_line_id=f"{last.id}", serials=[text],
        ))
        back.append(text)

    if movements:
        posting.post(db, posting.Posting(
            source_doc_type=f"{ISSUE_SOURCE}_BACK",
            source_doc_id=f"{order.id}-{uuid.uuid4().hex[:8]}",
            source_doc_number=order.order_number,
            movement_date=now_ist().date(),
            remarks=f"Order {order.order_number or order.id} put back: serials returned to their store",
            movements=movements,
        ))
    return back


def add_accepted_to_catalogue(grn, db: Session, undo: list) -> list:
    """Goods accepted on a GRN, added to the Inventory list. Every change is
    appended to ``undo`` as it is made, so the caller can reverse all of it
    if this fails part way or its commit fails.

    A serial already on a catalogue record (a customer return) is put back
    on the shelf; a new serial gets its own record copied from the
    product's; counted goods add to the product's counted record.
    """

    from app.services.inventory_service import InventoryService

    items = InventoryService.items_col
    company_id = str(grn.company_id)

    for line in grn.lines:
        accepted = Decimal(line.qty_accepted or 0)
        if accepted <= 0:
            continue
        model = {"$regex": f"^{re.escape(line.sku)}$", "$options": "i"}
        scope = {"attributes.model_number": model, "company_id": {"$in": [company_id, None]}}
        template = items.find_one({**scope}, sort=[("company_id", -1)])
        if template is None:
            continue  # GRN lines are checked against the catalogue; nothing to copy from.

        for serial in line.serials or []:
            existing = items.find_one({"serial_number": serial, "company_id": {"$in": [company_id, None]}})
            if existing is not None:
                before = {k: existing.get(k) for k in ("attributes", "issued", "issued_order", "issued_order_id", "issued_at")}
                attributes = dict(existing.get("attributes") or {})
                attributes["instock"] = 1
                items.update_one({"_id": existing["_id"]}, {
                    "$set": {"attributes": attributes},
                    "$unset": {"issued": "", "issued_order": "", "issued_order_id": "", "issued_at": ""},
                })
                undo.append(("restore", existing["_id"], before))
                continue
            record = {k: v for k, v in template.items() if k not in ("_id", "issued", "issued_order", "issued_order_id",
                                                                       "issued_at")}
            record["serial_number"] = serial
            record["company_id"] = template.get("company_id") or company_id
            record["attributes"] = {**(template.get("attributes") or {}), "instock": 1, "stock": 1}
            record["received_on_grn"] = grn.grn_number
            undo.append(("delete", items.insert_one(record).inserted_id, None))

        if not line.serials:
            counted = items.find_one({**scope, "serial_number": {"$in": ["", None]}, "issued": {"$ne": True}})
            if counted is not None:
                attributes = dict(counted.get("attributes") or {})
                field = "instock" if "instock" in attributes or "stock" not in attributes else "stock"
                before = {"attributes": dict(attributes)}
                attributes[field] = float(Decimal(str(attributes.get(field) or 0)) + accepted)
                items.update_one({"_id": counted["_id"]}, {"$set": {"attributes": attributes}})
                undo.append(("restore", counted["_id"], before))
            else:
                record = {k: v for k, v in template.items() if k not in ("_id", "issued", "issued_order",
                                                                           "issued_order_id", "issued_at")}
                record["serial_number"] = ""
                record["company_id"] = template.get("company_id") or company_id
                record["attributes"] = {**(template.get("attributes") or {}), "instock": float(accepted),
                                        "stock": float(accepted)}
                record["received_on_grn"] = grn.grn_number
                undo.append(("delete", items.insert_one(record).inserted_id, None))
    return undo


def undo_catalogue(undo: list) -> None:
    from app.services.inventory_service import InventoryService

    items = InventoryService.items_col
    for action, item_id, before in reversed(undo):
        if action == "delete":
            items.delete_one({"_id": item_id})
        else:
            unset = {k: "" for k, v in before.items() if v is None and k != "attributes"}
            change = {"$set": {k: v for k, v in before.items() if v is not None}}
            if unset:
                change["$unset"] = unset
            items.update_one({"_id": item_id}, change)


# ----------------------------------------------------------------------
# Goods counted by quantity (no serial numbers)
# ----------------------------------------------------------------------
def take_counted_out(order, item: dict, quantity: float, line_index: int, db: Session) -> Decimal:
    """Take a counted product out of its company's stores, main store first.

    Does not commit. The Inventory list cannot name a store for counted
    goods, so they come from the main store and then the others, as far as
    there is stock; what the stores did not hold is logged and returned as
    the shortfall (the Inventory list also stops at nothing).
    """

    import logging

    from app.models.stock import StockBalance

    company_id = item.get("company_id") or (str(order.selling_company_id) if order.selling_company_id else None)
    sku = str((item.get("attributes") or {}).get("model_number") or "").strip().upper()
    want = Decimal(str(quantity))
    if not company_id or not sku or want <= 0:
        return want

    balances = (
        db.query(StockBalance, Location)
        .join(Location, Location.id == StockBalance.warehouse_id)
        .filter(StockBalance.company_id == uuid.UUID(str(company_id)), StockBalance.sku == sku,
                StockBalance.stock_status == UNRESTRICTED)
        .order_by(Location.is_default.desc(), Location.location_name.asc())
        .all()
    )
    movements = []
    left = want
    for balance, _store in balances:
        # Units carrying a serial are not counted stock: only what is left
        # after them can be taken by quantity.
        serialised = db.query(StockSerial).filter(
            StockSerial.company_id == balance.company_id, StockSerial.warehouse_id == balance.warehouse_id,
            StockSerial.bin_id == balance.bin_id, StockSerial.sku == sku, StockSerial.status == SERIAL_IN_STOCK,
        ).count()
        free = Decimal(balance.qty_on_hand or 0) - Decimal(balance.qty_reserved or 0) - Decimal(serialised)
        take = min(free, left)
        if take <= 0:
            continue
        movements.append(posting.Movement(
            company_id=balance.company_id, warehouse_id=balance.warehouse_id, bin_id=balance.bin_id, sku=sku,
            stock_status=UNRESTRICTED, quantity=-take, movement_type=SALES_DISPATCH,
            source_line_id=f"{line_index}:{len(movements) + 1}",
        ))
        left -= take
        if left <= 0:
            break

    if movements:
        posting.post(db, posting.Posting(
            source_doc_type=ISSUE_SOURCE,
            source_doc_id=f"{order.id}-{uuid.uuid4().hex[:8]}",
            source_doc_number=order.order_number,
            movement_date=now_ist().date(),
            remarks=f"Issued by quantity to {order.order_number or order.id}",
            movements=movements,
        ))
    if left > 0:
        logging.getLogger(__name__).warning(
            "Order %s: %s of %s %s issued but not held in any store; the stores were short",
            order.order_number, left.normalize(), want.normalize(), sku,
        )
    return left


def put_counted_back(order, db: Session) -> Decimal:
    """Put back whatever counted goods this order still has out of its stores.

    Worked out from the ledger itself - every ORDER_ISSUE out less every
    put-back - so it puts back exactly once however often it is called.
    Does not commit. Returns the quantity put back.
    """

    from sqlalchemy import func, or_

    rows = (
        db.query(StockLedger.company_id, StockLedger.warehouse_id, StockLedger.bin_id, StockLedger.sku,
                 func.sum(StockLedger.quantity))
        .filter(or_(StockLedger.source_doc_type == ISSUE_SOURCE, StockLedger.source_doc_type == f"{ISSUE_SOURCE}_BACK"),
                StockLedger.source_doc_id.like(f"{order.id}-%"), StockLedger.serial_no.is_(None))
        .group_by(StockLedger.company_id, StockLedger.warehouse_id, StockLedger.bin_id, StockLedger.sku)
        .all()
    )
    movements = [
        posting.Movement(
            company_id=company_id, warehouse_id=warehouse_id, bin_id=bin_id, sku=sku, stock_status=UNRESTRICTED,
            quantity=-Decimal(net), movement_type=REVERSAL, source_line_id=str(n),
        )
        for n, (company_id, warehouse_id, bin_id, sku, net) in enumerate(rows, start=1)
        if Decimal(net or 0) < 0
    ]
    if not movements:
        return Decimal(0)
    posting.post(db, posting.Posting(
        source_doc_type=f"{ISSUE_SOURCE}_BACK",
        source_doc_id=f"{order.id}-{uuid.uuid4().hex[:8]}",
        source_doc_number=order.order_number,
        movement_date=now_ist().date(),
        remarks=f"Order {order.order_number or order.id} put back: counted goods returned to their store",
        movements=movements,
    ))
    return sum((m.quantity for m in movements), Decimal(0))
