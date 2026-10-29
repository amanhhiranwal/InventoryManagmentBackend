"""The warehouse's own half of the job: the catalogue, and the shelf.

Inventory could see what an order wanted and what was on hand, but the two
never met - dispatching four panels left the shelf still claiming twelve.
This covers both sides of putting that right:

  * the procurement role keeps the catalogue: add a product, correct a
    stock figure, remove it again, and see the totals - and a salesperson,
    who may still add a product for their own company, cannot correct a
    count or delete one;
  * a bulk record - one serial standing for many units - cannot be
    issued by serial, so production cannot mark its order ready; a super
    admin moving the order on counts it down by quantity instead, at
    ready, and it comes back if the order is held afterwards;
  * the deduction is written down and cannot happen twice.

Run seed_sales_team.py and seed_fulfilment_roles.py first. Everything it
creates is removed again.

    docker exec -w /app backend_app python test_stock_movements.py
"""

import sys
import uuid
from datetime import datetime, timezone

from seed_sales_team import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    TEAM_PASSWORD,
    api,
    coordinate_order,
    email_for,
    login,
    rows,
    sign_off_order,
)

TAG = uuid.uuid4().hex[:6]
SERIAL = f"TEST-{TAG}".upper()

passed, failed = [], []
section = ""


def check(name, condition, detail=""):
    (passed if condition else failed).append(f"{section}: {name}")
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + name
        + (f"   [{detail}]" if detail and not condition else "")
    )


def banner(title):
    global section
    section = title
    print(f"\n{title}")


token = {}
created_orders = []
created_invoices = []
created_items = []

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    token["inventory"] = login("inventory@mailinator.com", TEAM_PASSWORD)
    token["accounts"] = login("accounts@mailinator.com", TEAM_PASSWORD)
    token["production"] = login("production@mailinator.com", TEAM_PASSWORD)
    token["logistics"] = login("logistics@mailinator.com", TEAM_PASSWORD)
    token["am_north_1"] = login(email_for("am_north_1"), TEAM_PASSWORD)

    inventory = token["inventory"]
    production = token["production"]
    logistics = token["logistics"]
    print("Signed in as the super admin, the catalogue, the desks and a salesperson.")

    types = {t["code"]: t for t in rows(api("get", "/product-types/", admin))}
    product_type = types.get("DISPLAY") or next(iter(types.values()), None)

    companies = rows(api("get", "/inventory/companies", inventory))

    # ================================================== the catalogue
    banner("1. The warehouse keeps the catalogue")

    check("the procurement role is offered a company to file under", len(companies) >= 1, str(companies))

    body = {
        "name": f"Test Panel {TAG}",
        "serial_number": SERIAL,
        "product_type_code": product_type["code"],
        "category": product_type.get("category") or "General",
        "attributes": {
            "rate": 50000, "unit": "Nos", "instock": 10, "case_size": 1,
            "model_number": f"MDL-{TAG}",
            "specification": "Seeded by the stock movement suite",
        },
        "company_id": str(companies[0]["id"]) if companies else None,
    }

    created = api("post", "/inventory/items", inventory, json=body)
    check("they can add a product", created.status_code == 200, f"{created.status_code} {created.text[:160]}")

    item = created.json()["data"] if created.status_code == 200 else None

    if item:
        created_items.append(item["_id"])

        check("it starts with the stock they gave it", float(item["attributes"]["instock"]) == 10, str(item["attributes"]))

        body["attributes"]["instock"] = 12
        updated = api("put", f"/inventory/items/{item['_id']}", inventory, json=body)
        check("they can correct the count", updated.status_code == 200, f"{updated.status_code} {updated.text[:140]}")
        check(
            "and the new figure sticks",
            updated.status_code == 200
            and float(updated.json()["data"]["attributes"]["instock"]) == 12,
            updated.text[:120],
        )

    # Sales have always been able to add a product for their own company,
    # and that stays. Correcting a stock figure is the warehouse's call,
    # and that is what they are kept out of.
    if item:
        refused = api("put", f"/inventory/items/{item['_id']}", token["am_north_1"], json=body)
        check(
            "a salesperson cannot correct a stock count",
            refused.status_code == 403,
            f"got {refused.status_code}",
        )

        refused = api("delete", f"/inventory/items/{item['_id']}", token["am_north_1"])
        check(
            "nor remove a product from the catalogue",
            refused.status_code == 403,
            f"got {refused.status_code}",
        )

    # The desk reports what is on the shelf.
    desk = api("get", "/fulfilment/production", production).json()["data"]
    kpis = {k["key"]: k["value"] for k in desk["kpis"]}

    check("the desk counts the units on hand", kpis["units_in_stock"] >= 12, str(kpis.get("units_in_stock")))
    check("and the products with nothing left", "out_of_stock" in kpis, str(list(kpis)))

    # ================================================== an order for it
    banner("2. An order for four of them")

    now = datetime.now(timezone.utc)

    raised = api("post", "/orders", token["am_north_1"], json={
        "customer_name": f"Stock Test {TAG}",
        "company_name": "Synergy North Agro",
        "state": "Delhi",
        "order_date": now.isoformat(),
        "items": [{
            "product": f"Test Panel {TAG}",
            "sku": SERIAL,
            "qty": 4,
            "rate": 50000,
            "tax_rate": 18,
        }],
    })
    check("the order is raised", raised.status_code == 200, f"{raised.status_code} {raised.text[:150]}")

    order = raised.json()["data"]
    created_orders.append(order["id"])

    def stock_now():
        for row in rows(api("get", "/inventory/items", inventory)):
            if str(row.get("serial_number") or "").upper() == SERIAL:
                return float((row.get("attributes") or {}).get("instock") or 0)
        return None

    def status_now():
        return api("get", f"/orders/{order['id']}", admin).json()["data"]["status"]

    # An order is not confirmed by saying so any more: the approval chain
    # stands in front of it, whatever the discount. This suite is about
    # what stock does afterwards, so the chain is cleared rather than
    # restated - see sign_off_order, and test_approval_chain for the chain
    # itself.
    sign_off_order(order, token["am_north_1"])

    api("put", f"/orders/{order['id']}/status", token["am_north_1"], json={"status": "CONFIRMED"})

    # The money is not this suite's subject, but accounts will not pass an
    # order with nothing recorded against it - so raise the invoice and pay
    # the advance, the same way a real order reaches the warehouse.
    invoice = api("post", "/proforma-invoices", token["am_north_1"], json={
        "sales_order_id": order["id"],
        "status": "DRAFT",
        "advance_percent": 30,
    }).json()["data"]
    created_invoices.append(invoice["id"])

    api("put", f"/proforma-invoices/{invoice['id']}", token["am_north_1"], json={
        "amount_paid": round(float(invoice["grand_total"]) * 0.3, 2),
    })

    # The business coordinator accepts the advance and processes the
    # order before accounts see it.
    coordinate_order(order["id"])

    api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Paid.",
    })

    check("accounts release it to the warehouse", status_now() == "PAYMENT_VERIFIED", status_now())
    check("the shelf is untouched so far", stock_now() == 12, str(stock_now()))

    # ================================================== picking it
    banner("3. Picking does not move stock")

    api("put", f"/fulfilment/orders/{order['id']}/decide", production, json={
        "approve": True, "remarks": "Taken in.",
    })
    check("it goes into procurement", status_now() == "PROCUREMENT", status_now())
    check("the shelf is still untouched", stock_now() == 12, str(stock_now()))

    # One serial on a record holding twelve cannot name four units.
    refused = api("put", f"/fulfilment/orders/{order['id']}/decide", production, json={
        "approve": True, "remarks": "Picked and packed.",
        "serials": [{"line": 0, "serial": SERIAL}],
    })
    check("production cannot mark it ready without a serial per unit", refused.status_code == 400, f"{refused.status_code} {refused.text[:120]}")
    check("and the shelf is still untouched", stock_now() == 12, str(stock_now()))

    # ================================================== the fallback
    banner("4. Moved on by a super admin, it is counted down by quantity at ready")

    api("put", f"/orders/{order['id']}/status", admin, json={"status": "READY"})
    check("it is ready to dispatch", status_now() == "READY", status_now())
    check("four have come off the shelf", stock_now() == 8, str(stock_now()))

    api("put", f"/orders/{order['id']}/status", admin, json={"status": "DISPATCHED"})
    check("dispatching it does not take them again", stock_now() == 8, str(stock_now()))

    desk = api("get", "/fulfilment/logistics", logistics).json()["data"]
    mine = next((o for o in desk["orders"] if o["id"] == order["id"]), None)
    movements = (mine or {}).get("stock_movements") or []

    check("the movement is on the record", len(movements) == 1, str(movements))

    if movements:
        move = movements[0]
        check("it says what left", move["direction"] == "OUT" and move["quantity"] == 4, str(move))
        check("and what was left behind", move["stock_before"] == 12 and move["stock_after"] == 8, str(move))
        check("and who did it", bool(move.get("actor")), str(move.get("actor")))

    # ================================================== not twice
    banner("5. It cannot happen twice")

    api("put", f"/orders/{order['id']}/status", logistics, json={"status": "ON_HOLD", "remarks": "Recalled."})
    check("the order goes on hold", status_now() == "ON_HOLD", status_now())
    check("and the stock comes back", stock_now() == 12, str(stock_now()))

    api("put", f"/orders/{order['id']}/status", admin, json={"status": "DISPATCHED"})
    check("dispatched again", status_now() == "DISPATCHED", status_now())
    check(
        "the shelf is not raided a second time",
        stock_now() == 12,
        f"{stock_now()} - a second deduction would leave 8",
    )

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    banner("6. Clearing what the test created")

    try:
        for item_id in created_items:
            api("delete", f"/inventory/items/{item_id}", admin)

        from sqlalchemy import text

        from app.database.mongodb import sync_mongo_db
        from app.database.postgres import SessionLocal

        if created_orders:
            sync_mongo_db["inventory_movements"].delete_many(
                {"order_id": {"$in": [int(i) for i in created_orders]}}
            )

        session = SessionLocal()

        def run(sql, **params):
            try:
                session.execute(text(sql), params)
            except Exception as exc:
                session.rollback()
                print("   could not clear:", str(exc).split("\n")[0][:90])

        if created_invoices:
            run("delete from notifications where module = 'proforma_invoice' and entity_id = any(:ids)", ids=created_invoices)
            run("delete from sales_proforma_invoice_activity where proforma_invoice_id = any(:ids)", ids=created_invoices)
            run("delete from sales_proforma_invoice where id = any(:ids)", ids=created_invoices)

        if created_orders:
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=created_orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)

        session.commit()
        session.close()

        print(
            f"   removed {len(created_orders)} orders, "
            f"{len(created_invoices)} invoices, "
            f"{len(created_items)} products and their movements"
        )
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
