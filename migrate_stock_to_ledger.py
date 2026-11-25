"""Bring today's stock counts from MongoDB into the stock ledger, once.

Products stay in MongoDB. What each company holds moves to PostgreSQL as
one posted opening stock document per company, in that company's default
warehouse, main store bin:

  * a catalogue record with a serial number holding 1 becomes that serial,
    on shelf;
  * a record holding a count (no serial, or a count above one) becomes
    that quantity;
  * records already issued to an order, holding nothing, or with no model
    number are left out - and listed.

Two things cannot be moved without a decision, and are listed instead:
  * shared products (no company) - stock cannot be shared, so a super
    admin files each under a company first;
  * a company with no default warehouse - set one on the Locations screen.

Dry run by default: it prints what it would post and changes nothing.

    docker exec -w /app backend_app python migrate_stock_to_ledger.py
    docker exec -w /app backend_app python migrate_stock_to_ledger.py --apply

Running it again after --apply does nothing for a warehouse that already
has a migrated document. Stock the Inventory form has already put into the
ledger (serials, and quantities posted as PRODUCT_FORM) is not added again.
"""

import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func

from app.core.stock import OPENING, OPENING_BALANCE, POSTED, UNRESTRICTED
from app.core.timezone import now_ist
from app.core.warehouse import WAREHOUSE
from app.database.postgres import SessionLocal
from app.models.company import Company
from app.models.location import Location
from app.models.stock import StockDocument, StockDocumentLine, StockLedger, StockSerial
from app.models.warehouse import WarehouseBin
from app.services.inventory_service import InventoryService
from app.services.procurement.common import next_reference
from app.services.stock import posting
from app.services.warehouse_service import WarehouseService

REASON = "Migrated from MongoDB stock counts"
ACTOR = "System migration"


def held(item) -> Decimal:
    attributes = item.get("attributes") or {}
    value = attributes.get("instock")
    if value is None:
        value = attributes.get("stock")
    try:
        return Decimal(str(value or 0))
    except Exception:  # noqa: BLE001 - a bad figure is reported as nothing held
        return Decimal(0)


def main(apply: bool) -> int:
    db = SessionLocal()
    skipped = defaultdict(list)
    plans = {}

    companies = {str(c.id): c for c in db.query(Company).all()}

    for item in InventoryService.items_col.find({}, {"image_base64": 0}):
        name = item.get("name") or "?"
        sku = str((item.get("attributes") or {}).get("model_number") or "").strip().upper()
        quantity = held(item)
        company_id = item.get("company_id")

        if item.get("issued"):
            continue
        if quantity <= 0:
            continue
        if not sku:
            skipped["no model number"].append(name)
            continue
        if not company_id:
            skipped["shared product (no company) - file it under a company first"].append(f"{sku} {name}")
            continue
        if company_id not in companies:
            skipped["company no longer exists"].append(f"{sku} {name}")
            continue

        plan = plans.setdefault(company_id, {"serials": defaultdict(list), "counts": defaultdict(Decimal), "names": {}})
        plan["names"][sku] = name
        serial = str(item.get("serial_number") or "").strip().upper()

        if serial and quantity == 1:
            plan["serials"][sku].append(serial)
        else:
            plan["counts"][sku] += quantity

    posted = 0
    for company_id, plan in plans.items():
        company = companies[company_id]
        warehouse = (
            db.query(Location)
            .filter(
                Location.company_id == company.id,
                Location.is_default.is_(True),
                (Location.location_type.is_(None)) | (Location.location_type == WAREHOUSE),
            )
            .first()
        )
        if warehouse is None:
            skipped["company has no default warehouse"].append(company.company_name)
            continue

        done = (
            db.query(StockDocument)
            .filter(StockDocument.warehouse_id == warehouse.id, StockDocument.reason == REASON)
            .first()
        )
        if done is not None:
            print(f"{company.company_name}: already migrated as {done.doc_number}; left alone.")
            continue

        WarehouseService.ensure_default_bins(db, warehouse)
        db.flush()
        store = (
            db.query(WarehouseBin)
            .filter(WarehouseBin.warehouse_id == warehouse.id, WarehouseBin.code == "STORE")
            .one()
        )

        # Stock the Inventory form already put into the ledger (PRODUCT_FORM)
        # is in MongoDB too; it is not brought across a second time.
        in_ledger = {
            serial
            for (serial,) in db.query(StockSerial.serial_no).filter(StockSerial.company_id == company.id)
        }
        counted = {
            sku_: Decimal(total or 0)
            for sku_, total in db.query(StockLedger.sku, func.sum(StockLedger.quantity))
            .filter(StockLedger.company_id == company.id, StockLedger.serial_no.is_(None),
                    # Accepted on a GRN, which also adds them to the Inventory list.
                    (StockLedger.source_doc_type == "PRODUCT_FORM")
                    | ((StockLedger.source_doc_type == "GRN_QC") & (StockLedger.stock_status == UNRESTRICTED)
                       & (StockLedger.quantity > 0)))
            .group_by(StockLedger.sku)
        }

        lines = []
        for sku in sorted(set(plan["serials"]) | set(plan["counts"])):
            if plan["serials"].get(sku):
                serials = sorted(set(plan["serials"][sku]))
                if len(serials) != len(plan["serials"][sku]):
                    skipped["duplicate serial in MongoDB (kept once)"].append(sku)
                fresh = [s for s in serials if s not in in_ledger]
                if len(fresh) != len(serials):
                    skipped["serials already in the ledger (left as they are)"].append(f"{sku}: {len(serials) - len(fresh)}")
                if fresh:
                    lines.append((sku, Decimal(len(fresh)), fresh))
            if plan["counts"].get(sku):
                rest = plan["counts"][sku] - counted.get(sku, Decimal(0))
                if rest != plan["counts"][sku]:
                    skipped["quantity already in the ledger from the Inventory form (not added again)"].append(
                        f"{sku}: {(plan['counts'][sku] - max(rest, Decimal(0))).normalize()}")
                if rest > 0:
                    lines.append((sku, rest, []))

        units = sum(q for _, q, _ in lines)
        print(f"\n{company.company_name} -> {warehouse.location_name} ({warehouse.location_code}), bin STORE")
        for sku, quantity, serials in lines:
            print(f"  {sku:24s} {str(quantity.normalize()):>8s}  {plan['names'][sku][:40]}"
                  + (f"  [{len(serials)} serials]" if serials else ""))
        print(f"  {len(lines)} line(s), {units.normalize()} unit(s)")

        if not apply:
            continue

        doc = StockDocument(
            doc_number=next_reference(db, StockDocument, "doc_number", f"{(company.company_code or 'CO').upper()}-OPN"),
            doc_type=OPENING,
            company_id=company.id,
            warehouse_id=warehouse.id,
            status=POSTED,
            movement_date=now_ist().date(),
            reason=REASON,
            created_by_name=ACTOR,
            approved_by_name=ACTOR,
            submitted_at=datetime.utcnow(),
            posted_at=datetime.utcnow(),
            lines=[
                StockDocumentLine(
                    line_no=i, sku=sku, product_name=plan["names"][sku], bin_id=store.id,
                    quantity=quantity, serials=serials or None,
                )
                for i, (sku, quantity, serials) in enumerate(lines, start=1)
            ],
        )
        db.add(doc)
        db.flush()

        posting.post(
            db,
            posting.Posting(
                source_doc_type="STOCK_DOCUMENT",
                source_doc_id=str(doc.id),
                source_doc_number=doc.doc_number,
                movement_date=doc.movement_date,
                created_by_name=ACTOR,
                approved_by_name=ACTOR,
                remarks=REASON,
                movements=[
                    posting.Movement(
                        company_id=company.id, warehouse_id=warehouse.id, bin_id=store.id,
                        sku=line.sku, product_name=line.product_name, stock_status=UNRESTRICTED,
                        quantity=Decimal(line.quantity), movement_type=OPENING_BALANCE,
                        source_line_id=str(line.id), serials=list(line.serials or []),
                    )
                    for line in doc.lines
                ],
            ),
        )
        db.commit()
        posted += 1
        print(f"  posted as {doc.doc_number}")

    if skipped:
        print("\nNot moved:")
        for why, names in skipped.items():
            print(f"  {why}: {len(names)}")
            for name in names[:15]:
                print(f"    - {name}")
            if len(names) > 15:
                print(f"    ... and {len(names) - 15} more")

    print("\n" + (f"Posted {posted} document(s)." if apply else "Dry run: nothing was changed. Run with --apply to post."))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv[1:]))
