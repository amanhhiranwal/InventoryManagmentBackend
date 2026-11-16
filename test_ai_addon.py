"""The AI add-on: one price per Interactive Flat Panel, set in Masters.

A panel line with AI set to Yes is charged on its own "AI" line (SKU
ADDON:AI), whose quantity is the panels on AI added up:

  * only a super admin sets the AI price; anyone signed in can read it;
  * the AI line is priced from that rate on the sales order and proposal,
    and may be sold above it (marked up) like any line;
  * only an Interactive Flat Panel may take AI - an OPS module is refused;
  * the AI line must match the panels on AI, unit for unit, and cannot be
    there with none (or missing with some);
  * a document with no AI anywhere is priced exactly as before.

The AI price is put back to what it was, and every order made is removed.

    docker exec -w /app backend_app python test_ai_addon.py
"""

import sys
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from seed_sales_team import TEAM_PASSWORD, api, email_for, login
from app.services import sales_order_service as sos
from app.services.quotation_service import compute_totals

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

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
panel = next((k for k, v in catalogue.items() if k and v.get("type") in sos.AI_PRODUCT_TYPES and (v.get("ecp") or 0) > 0), None)
other = next((k for k, v in catalogue.items() if k and v.get("type") and v.get("type") not in sos.AI_PRODUCT_TYPES), None)

if not panel or not other:
    print(f"Need an Interactive Flat Panel and another product in inventory (panel={panel}, other={other}).")
    sys.exit(1)

admin = login(*ADMIN)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
created_orders = []
original = api("get", "/warranty-terms/ai", admin).json()["data"]["price"]

try:
    print("\nThe AI price on the Warranty Terms master")
    r = api("put", "/warranty-terms/ai", salesperson, json={"price": 1})
    check("a salesperson cannot set it", r.status_code == 403, str(r.status_code))
    r = api("put", "/warranty-terms/ai", admin, json={"price": -5})
    check("a negative price is refused", r.status_code == 422, str(r.status_code))
    r = api("put", "/warranty-terms/ai", admin, json={"price": 4500})
    check("the super admin sets it", r.status_code == 200 and r.json()["data"]["price"] == 4500, r.text[:120])
    read = api("get", "/warranty-terms/ai", salesperson).json()["data"]["price"]
    check("and a salesperson reads it back", read == 4500, str(read))

    print("\nThe rule, on the pricing itself")
    check("an Interactive Flat Panel takes AI", sos.takes_ai(panel), panel)
    check(f"{other} does not", not sos.takes_ai(other), other)
    check("the AI line is priced from Masters", sos.rate_for_sku(sos.AI_SKU, "ECP") == 4500)
    check("and the same at dealer price", sos.rate_for_sku(sos.AI_SKU, "DP") == 4500)

    lines = [
        {"sku": panel, "quantity": 3, "unit_price": 0, "discount": 0, "ai": True},
        {"sku": sos.AI_SKU, "quantity": 3, "unit_price": 0, "discount": 0},
    ]
    totals = compute_totals(lines, price_type="ECP")
    check("a proposal prices the AI line at 3 x 4500",
          lines[1]["unit_price"] == 4500, str(lines[1]))
    check("and it is in the subtotal",
          round(totals["subtotal"], 2) == round(3 * catalogue[panel]["ecp"] + 3 * 4500, 2), str(totals["subtotal"]))

    why = refused(compute_totals, [{"sku": other, "quantity": 1, "unit_price": 0, "ai": True},
                                   {"sku": sos.AI_SKU, "quantity": 1, "unit_price": 0}], price_type="ECP")
    check("AI on a product that is not a panel is refused", bool(why) and "not an Interactive Flat Panel" in str(why), str(why))
    why = refused(compute_totals, [{"sku": panel, "quantity": 1, "unit_price": 0},
                                   {"sku": sos.AI_SKU, "quantity": 1, "unit_price": 0}], price_type="ECP")
    check("an AI line with no panel on AI is refused", bool(why), str(why))
    why = refused(compute_totals, [{"sku": panel, "quantity": 2, "unit_price": 0, "ai": True}], price_type="ECP")
    check("panels on AI with no AI line are refused", bool(why), str(why))
    why = refused(compute_totals, [{"sku": panel, "quantity": 2, "unit_price": 0, "ai": True},
                                   {"sku": sos.AI_SKU, "quantity": 1, "unit_price": 0}], price_type="ECP")
    check("an AI line that does not match the panels is refused", bool(why) and "must match" in str(why), str(why))

    plain = [{"sku": panel, "quantity": 2, "unit_price": 0}]
    before = compute_totals(plain, price_type="ECP")
    check("a proposal with no AI is priced as before",
          round(before["subtotal"], 2) == round(2 * catalogue[panel]["ecp"], 2), str(before["subtotal"]))

    print("\nA sales order, through the API")
    order = api("post", "/orders", salesperson, json={
        "customer_name": f"AI Addon {TAG}",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [
            {"sku": panel, "product": "Interactive Flat Panel", "qty": 2, "rate": 0, "tax_rate": 18, "ai": True},
            {"sku": sos.AI_SKU, "product": "AI", "qty": 2, "rate": 0, "tax_rate": 18},
        ],
    })
    check("an order with two panels on AI is saved", order.status_code == 200, order.text[:160])
    if order.status_code == 200:
        data = order.json()["data"]
        created_orders.append(data["id"])
        # The server prices the order's totals from the price list (the
        # screen sends the same figures on each line).
        expected = round(2 * catalogue[panel]["ecp"] + 2 * 4500, 2)
        check("the order is priced with AI at the Masters price",
              round(float(data["total_amount"]), 2) == expected, f"{data['total_amount']} vs {expected}")
        check("the panel keeps its AI flag", data["items"][0].get("ai") is True, str(data["items"][0]))

    marked = api("post", "/orders", salesperson, json={
        "customer_name": f"AI Marked Up {TAG}",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [
            {"sku": panel, "product": "Interactive Flat Panel", "qty": 1, "rate": 0, "tax_rate": 18, "ai": True},
            {"sku": sos.AI_SKU, "product": "AI", "qty": 1, "rate": 5000, "tax_rate": 18, "marked_up": True},
        ],
    })
    check("the AI line can be sold above its Masters price", marked.status_code == 200, marked.text[:160])
    if marked.status_code == 200:
        data = marked.json()["data"]
        created_orders.append(data["id"])
        expected = round(catalogue[panel]["ecp"] + 5000, 2)
        check("priced at the AI selling price, not the Masters price",
              round(float(data["total_amount"]), 2) == expected, f"{data['total_amount']} vs {expected}")

    wrong = api("post", "/orders", salesperson, json={
        "customer_name": f"AI Wrong {TAG}",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [
            {"sku": other, "product": "OPS", "qty": 1, "rate": 0, "tax_rate": 18, "ai": True},
            {"sku": sos.AI_SKU, "product": "AI", "qty": 1, "rate": 0, "tax_rate": 18},
        ],
    })
    check("an order with AI on a non-panel is refused", wrong.status_code == 400, f"{wrong.status_code} {wrong.text[:160]}")
    if wrong.status_code == 200:
        created_orders.append(wrong.json()["data"]["id"])
finally:
    api("put", "/warranty-terms/ai", admin, json={"price": original})
    print(f"\n   AI price put back to {original}")
    if created_orders:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()
        session.execute(text("delete from notifications where module = 'sales_order' and entity_id = any(:ids)"), {"ids": created_orders})
        session.execute(text("delete from sales_order_activity where sales_order_id = any(:ids)"), {"ids": created_orders})
        session.execute(text("delete from sales_order where id = any(:ids)"), {"ids": created_orders})
        session.commit()
        session.close()
        print(f"   removed {len(created_orders)} orders")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
