"""Put the quantity and the HSN back on lines saved before they were kept.

Two things were lost on the way onto a document:

  * the quantity was written under ``quantity_case`` and read back under
    ``qty``, so a line printed a quantity of zero and an amount of zero
    beside a total that was right;
  * the HSN never reached the line at all, so the invoice grouped its tax
    under a dash and fell back to a flat rate.

Both are settled when a line is saved now. This walks what was already
stored and settles it there too. Rates, discounts and totals are not
touched - only the two fields that were empty.

    docker exec -w /app backend_app python repair_lines.py --dry-run
    docker exec -w /app backend_app python repair_lines.py
"""

import copy
import sys

from app.database.postgres import SessionLocal
from app.models.proforma_invoice import ProformaInvoice
from app.models.quotation import Quotation
from app.models.sales_order import SalesOrder
from app.services.sales_order_service import _as_float, _hsn_for_sku

DRY = "--dry-run" in sys.argv

db = SessionLocal()
touched = {"quantity": 0, "hsn": 0}
rows_changed = 0

for model, label in (
    (Quotation, "proposals"),
    (SalesOrder, "orders"),
    (ProformaInvoice, "invoices"),
):
    changed = 0

    for record in db.query(model).all():
        # Deep-copied before anything is touched. Mutating the stored list
        # in place and assigning it back leaves SQLAlchemy comparing the
        # new value against itself, so it sees no change and emits no
        # UPDATE - the first run of this reported eight records written
        # and wrote none.
        items = copy.deepcopy(record.items or [])
        dirty = False

        for line in items:
            if not isinstance(line, dict):
                continue

            quantity = _as_float(
                line.get("qty") or line.get("quantity_case") or line.get("quantity")
            )

            if quantity and (
                _as_float(line.get("qty")) != quantity
                or _as_float(line.get("quantity_case")) != quantity
            ):
                line["qty"] = quantity
                line["quantity_case"] = quantity
                touched["quantity"] += 1
                dirty = True

            if not str(line.get("hsn") or "").strip():
                found = _hsn_for_sku(line.get("sku"))

                if found:
                    line["hsn"] = found
                    touched["hsn"] += 1
                    dirty = True

        if dirty:
            record.items = items
            changed += 1

    rows_changed += changed
    print(f"  {changed:4} {label} to repair")

if DRY:
    db.rollback()
    print("\n  dry run - nothing written")
else:
    db.commit()
    print(f"\n  written: {rows_changed} records")

print(
    f"  {touched['quantity']} lines had their quantity settled, "
    f"{touched['hsn']} gained an HSN"
)
