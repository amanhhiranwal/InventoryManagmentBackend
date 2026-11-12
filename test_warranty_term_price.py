"""A warranty term's price, sold as its own line on proposals and orders.

The Warranty Terms master carries a price per unit (before GST). The Add
Products pickers list the priced terms under a Warranty Terms tab, and one
is added as its own line with SKU "WARRANTY:<term name>":

  * only a super admin sets the price; a negative one is refused;
  * a warranty line is priced from the master, whatever the form sends;
  * quantity, discount and GST work on it like any other line;
  * an unknown term charges nothing;
  * the Warranty dropdown on a product line is a label only: picking a
    priced term there adds nothing.

The price on "4 Years" is put back as it was at the end.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_warranty_term_price.py
"""

import sys

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + name
        + (f"   [{detail}]" if detail and not condition else "")
    )


TERM = "4 Years"

admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
zonal = login("zh.north@mailinator.com", TEAM_PASSWORD)

terms = api("get", "/warranty-terms", admin).json()["data"]
four = next((t for t in terms if t["name"] == TERM), None)

if four is None:
    print(f"Needs an active '{TERM}' term; found {[t['name'] for t in terms]}")
    sys.exit(1)

original_price = four.get("price", 0) or 0

try:
    print("\nThe master")

    check("terms carry a price", "price" in four, str(four))

    refused = api("put", f"/warranty-terms/{four['id']}", zonal, json={"price": 6000})
    check("a salesperson cannot set it", refused.status_code == 403, f"{refused.status_code}")

    negative = api("put", f"/warranty-terms/{four['id']}", admin, json={"price": -1})
    check("a negative price is refused", negative.status_code == 422, f"{negative.status_code}")

    saved = api("put", f"/warranty-terms/{four['id']}", admin, json={"price": 6000})
    check("a super admin sets 6,000", saved.status_code == 200, f"{saved.status_code} {saved.text[:120]}")
    check("and it reads back", saved.json()["data"]["price"] == 6000, saved.text[:200])

    listed = next(t for t in api("get", "/warranty-terms", zonal).json()["data"] if t["name"] == TERM)
    check("salespeople see the price, for the picker", listed["price"] == 6000, str(listed))

    # Priced in this process, which reads the price fresh.
    from app.services import sales_order_service as sos
    from app.services.quotation_service import compute_totals

    print("\nWhat a warranty line is priced at")

    check("WARRANTY:4 Years is 6,000", sos.rate_for_sku(f"WARRANTY:{TERM}") == 6000)
    check("whatever the case", sos.rate_for_sku("warranty:4 years") == 6000)
    check("at a dealer price too", sos.rate_for_sku(f"WARRANTY:{TERM}", "DTP") == 6000)
    check("an unknown term charges nothing", sos.rate_for_sku("WARRANTY:9 Years") == 0)

    print("\nOn a proposal")

    line = {"sku": f"WARRANTY:{TERM}", "product": f"{TERM} Warranty", "quantity": 2, "unit_price": 1, "discount": 0}
    items = [line]
    quote = compute_totals(items, price_type="ECP", gst_percent=18)
    check("2 units at 6,000, not the 1 the form sent", round(quote["subtotal"]) == 12000, str(quote))
    check("the line is stored at 6,000", items[0]["unit_price"] == 6000, str(items[0]))

    discounted = compute_totals([{**line, "unit_price": 6000, "discount": 10}], price_type="ECP", gst_percent=18)
    check(
        "a line discount comes off it like any line",
        round(discounted["subtotal"] - discounted.get("discount_amount", 0)) == 10800,
        str(discounted),
    )

    print("\nOn a sales order")

    order = sos.compute_order_totals(
        [{"sku": f"WARRANTY:{TERM}", "product": f"{TERM} Warranty", "qty": 1, "rate": 99}],
        price_type="ECP", gst_percent=18,
    )
    check("1 unit at 6,000", round(order["total_amount"]) == 6000, str(order))
    check("GST is charged on it", round(order["grand_total"]) == 7080, str(order))

    print("\nThe dropdown on a product line is a label")

    check(
        "a priced term picked in the dropdown adds nothing",
        sos.warranty_uplift_for_sku("ANY-PRODUCT-SKU", TERM, 65500, 2) == 0,
    )

finally:
    restored = api("put", f"/warranty-terms/{four['id']}", admin, json={"price": original_price})
    print(f"\n(4 Years price put back to {original_price}: {restored.status_code})")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
