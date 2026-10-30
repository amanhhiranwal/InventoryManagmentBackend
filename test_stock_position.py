"""Stock Against This Order: a product whose every unit has gone out.

Production issues the last units of a model; the order's Stock panel
must then read that model as in the catalogue with nothing left on the
shelf, not as "Not in the catalogue".

  * every unit issued -> known, 0 in stock, short against the order;
  * one unit still on the shelf -> known, only that unit counted;
  * a SKU inventory has never held -> still unknown.

Reads a stand-in for the inventory collection, so it needs no database.

    docker exec -w /app backend_app python test_stock_position.py
"""

import sys
from types import SimpleNamespace

from app.services.fulfilment_desk_service import FulfilmentDeskService
from app.services.inventory_service import InventoryService

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


MODEL = "STOCK-POSITION-TEST-MODEL"


class Shelf:
    """Just enough of a collection for stock_for: find() hands back every record."""

    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, *args, **kwargs):
        return list(self.docs)


def unit(serial, issued):
    doc = {
        "name": "Stock Position Test Panel", "serial_number": serial,
        "attributes": {"model_number": MODEL, "instock": 0 if issued else 1},
    }
    if issued:
        doc.update({"issued": True, "issued_order": "SO-TEST"})
    return doc


def line_for(docs, sku, qty=2, product="Stock Position Test Panel"):
    InventoryService.items_col = Shelf(docs)
    order = SimpleNamespace(id=0, items=[{"sku": sku, "product": product, "qty": qty}])
    return FulfilmentDeskService.stock_for(order, None)[0]


real = InventoryService.items_col

try:
    print("\nEvery unit issued")
    line = line_for([unit("SPT-1", True), unit("SPT-2", True)], MODEL)
    check("the product is still in the catalogue", line["known"] is True, str(line))
    check("with nothing left on the shelf", line["available"] == 0, str(line))
    check("and short against what is ordered", line["short"] is True, str(line))

    print("\nOne unit still on the shelf")
    line = line_for([unit("SPT-1", True), unit("SPT-2", True), unit("SPT-3", False)], MODEL, qty=1)
    check("only the unit on the shelf is counted", line["available"] == 1, str(line))
    check("which covers one", line["short"] is False, str(line))

    print("\nA SKU inventory never held")
    line = line_for([unit("SPT-1", True)], "NO-SUCH-MODEL-ANYWHERE", product="No Such Product")
    check("is still not in the catalogue", line["known"] is False and line["available"] is None, str(line))

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    InventoryService.items_col = real
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"  FAILED  {name}")
    sys.exit(1 if failed else 0)
