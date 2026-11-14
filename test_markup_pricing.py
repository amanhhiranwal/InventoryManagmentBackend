"""Selling above the list price: a marked-up line takes no discount.

A salesperson may sell a product or a warranty for more than its list
price. The form marks the line (``marked_up``) and sends the selling price
as the unit price:

  * on a real catalogue product the selling price is kept, above the list
    price, and the list price is recorded beside it;
  * a marked-up line with a discount is refused, on the opportunity, the
    proposal and the sales order;
  * with any marked-up line the document takes no overall discount;
  * the flag cannot lower a price: at or below the list price the line is
    priced from the list as before;
  * a line without the flag is priced exactly as before, whatever is sent;
  * the proforma keeps the marked-up price rather than re-reading the list.

Pure pricing checks, nothing is written.

    docker exec -w /app backend_app python test_markup_pricing.py
"""

import sys

from fastapi import HTTPException

from app.services import sales_order_service as sos
from app.services.opportunity_service import compute_product_totals
from app.services.quotation_service import compute_totals

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except HTTPException as error:
        return error.status_code == 400 and error.detail
    return False


catalogue = sos._catalogue()
sku, entry = next(((k, v) for k, v in catalogue.items() if (v.get("ecp") or 0) > 0), (None, None))
if sku is None:
    print("No catalogue product with a price - nothing to test against.")
    sys.exit(1)
listed = entry["ecp"]
above = round(listed + 5000, 2)

print(f"\nA proposal line on {sku} (list {listed})")

items = [{"sku": sku, "quantity": 2, "unit_price": above, "discount": 0, "marked_up": True}]
quote = compute_totals(items, price_type="ECP")
check("the selling price above the list is kept", items[0]["unit_price"] == above, str(items[0]))
check("the line is marked up, with the list price beside it",
      items[0]["marked_up"] is True and items[0]["list_price"] == listed, str(items[0]))
check("and costed at the selling price", round(quote["subtotal"], 2) == round(2 * above, 2), str(quote["subtotal"]))

why = refused(compute_totals, [{**items[0], "discount": 5}], price_type="ECP")
check("a discount on a marked-up line is refused", bool(why), str(why))
check("saying why", "above its list price" in str(why), str(why))
check("so is an overall discount on the document",
      bool(refused(compute_totals, [dict(items[0])], price_type="ECP", discount_input=500)))
check("an overall discount of 0 is fine",
      refused(compute_totals, [dict(items[0])], price_type="ECP", discount_input=0) is False)

low = [{"sku": sku, "quantity": 1, "unit_price": 1, "discount": 0, "marked_up": True}]
compute_totals(low, price_type="ECP")
check("the flag cannot lower the price: below the list it is priced from the list",
      low[0]["unit_price"] == listed and low[0]["marked_up"] is False, str(low[0]))

plain = [{"sku": sku, "quantity": 1, "unit_price": above, "discount": 10}]
compute_totals(plain, price_type="ECP")
check("without the flag a line is priced from the list as before, discount and all",
      plain[0]["unit_price"] == listed and plain[0]["marked_up"] is False, str(plain[0]))

print("\nA sales order line")

rows = [{"sku": sku, "qty": 1, "rate": above, "marked_up": True}]
order = sos.compute_order_totals(rows, price_type="ECP", gst_percent=18)
check("the order keeps the selling price", rows[0]["rate"] == above and round(order["total_amount"], 2) == above, str(rows[0]))
check("a discount on it is refused",
      bool(refused(sos.compute_order_totals, [{**rows[0], "discount": 3}], price_type="ECP")))
check("so is an overall discount",
      bool(refused(sos.compute_order_totals, [dict(rows[0])], price_type="ECP", discount_input=100)))

proforma = [dict(rows[0])]
sos.compute_order_totals(proforma, price_type=None)
check("the proforma bills the marked-up price, not the list", proforma[0]["rate"] == above, str(proforma[0]))

print("\nAn opportunity line")

check("a marked-up line with a discount is refused",
      bool(refused(compute_product_totals, [{"sku": sku, "quantity": 1, "unit_price": above, "discount": 5, "marked_up": True}])))
opp = compute_product_totals([{"sku": sku, "quantity": 1, "unit_price": above, "discount": 0, "tax": 18, "marked_up": True}])
check("and without one it is costed at the selling price", opp["products_subtotal"] == above, str(opp))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
