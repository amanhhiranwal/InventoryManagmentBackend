"""A typed selling price is billed exactly, on all three documents.

The screen turns a selling price into a discount percentage, which is what
every document stores and bills from. It used to round that percentage to
two places: 92,857 typed against a list price of 1,00,000 became 7.14%,
and the line billed 92,860. It now keeps the whole percentage and only
shows it to two places, so:

  * the opportunity, the proposal and the sales order each bill
    selling price x quantity, to the paisa;
  * the old two-place percentage is shown to bill something else, so the
    test fails if the rounding comes back.

The sales orders it makes through the API are removed again.

    docker exec -w /app backend_app python test_selling_price_rounding.py
"""

import sys
import uuid
from datetime import datetime, timezone

from app.services import sales_order_service as sos
from app.services.opportunity_service import compute_product_totals
from app.services.quotation_service import compute_totals
from seed_sales_team import TEAM_PASSWORD, api, email_for, login

TAG = uuid.uuid4().hex[:6]

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def screen_discount(list_price: float, selling: float) -> float:
    """lineMath.discountPercentFor, as the screen now works it out."""
    return (list_price - selling) / list_price * 100


def old_screen_discount(list_price: float, selling: float) -> float:
    """The same, rounded to two places as it used to be."""
    return round(screen_discount(list_price, selling), 2)


catalogue = sos._catalogue()
sku = next((k for k, v in catalogue.items() if k and (v.get("ecp") or 0) > 1000 and v.get("type") not in sos.AI_PRODUCT_TYPES), None)
if not sku:
    sku = next((k for k, v in catalogue.items() if k and (v.get("ecp") or 0) > 1000), None)
if not sku:
    print("Need a product with a list price in inventory.")
    sys.exit(1)

LIST = float(catalogue[sku]["ecp"])
# Selling prices that do not divide the list price evenly, at quantities
# that make a rounding error grow.
CASES = [
    (round(LIST * 0.92857, 0), 1),
    (round(LIST * 0.93567, 0) - 1, 25),
    (round(LIST * 0.8677, 0), 7),
]

salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
made = []

try:
    print(f"\nList price of {sku}: {LIST:,.2f}")

    print("\nThe old two-place percentage billed something else")
    drift = [
        abs((LIST * qty) * (1 - old_screen_discount(LIST, sell) / 100) - sell * qty)
        for sell, qty in CASES
    ]
    check("at least one case was off by a rupee or more", max(drift) >= 1, str(drift))

    for sell, qty in CASES:
        expected = round(sell * qty, 2)
        discount = screen_discount(LIST, sell)
        print(f"\nSelling {sell:,.2f} x {qty}  (discount {discount:.6f}%, shown {round(discount, 2)}%)")

        opp = compute_product_totals([{"sku": sku, "quantity": qty, "unit_price": LIST, "discount": discount, "tax": 18}])
        billed = round(opp["products_subtotal"] - opp["products_discount_amount"], 2)
        check("the opportunity bills the typed price", billed == expected, f"{billed} vs {expected}")

        quote = compute_totals([{"sku": sku, "quantity": qty, "unit_price": LIST, "discount": discount}], price_type="ECP")
        billed = round(quote["subtotal"] - quote["discount_amount"], 2)
        check("the proposal bills the typed price", billed == expected, f"{billed} vs {expected}")

        r = api("post", "/orders", salesperson, json={
            "customer_name": f"Rounding {TAG}",
            "order_date": datetime.now(timezone.utc).isoformat(),
            "items": [{"sku": sku, "product": sku, "qty": qty, "rate": LIST, "discount": discount, "tax_rate": 18}],
        })
        if r.status_code != 200:
            check("the sales order is saved", False, f"{r.status_code} {r.text[:160]}")
            continue
        data = r.json()["data"]
        made.append(data["id"])
        billed = round(float(data["total_amount"]) - float(data["discount_amount"]), 2)
        check("the sales order bills the typed price", billed == expected, f"{billed} vs {expected}")
        stored = float(data["items"][0].get("discount") or 0)
        check("and keeps the whole percentage", abs(stored - discount) < 1e-9, f"{stored} vs {discount}")
finally:
    from sqlalchemy import text

    from app.database.postgres import SessionLocal

    if made:
        session = SessionLocal()
        session.execute(text("delete from sales_order_activity where sales_order_id = any(:ids)"), {"ids": made})
        session.execute(text("delete from sales_approval where document_type = 'SALES_ORDER' and document_id = any(:ids)"), {"ids": made})
        session.execute(text("delete from sales_order where id = any(:ids)"), {"ids": made})
        session.commit()
        session.close()
    print(f"\n   removed {len(made)} test order(s)")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
