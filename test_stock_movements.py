"""The warehouse's own half of the job: the catalogue, and the shelf.

Inventory could see what an order wanted and what was on hand, but the two
never met - dispatching four panels left the shelf still claiming twelve.
This covers both sides of putting that right:

  * the procurement role keeps the catalogue: add a product, correct a
    stock figure, remove it again, and see the totals - and a salesperson,
    who may still add a product for their own company, cannot correct a
    count or delete one;
  * stock leaves the shelf when an order is dispatched, not before, and
    comes back if a dispatched order is cancelled;
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
    email_for,
    login,
    rows,
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
created_items = []

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    token["inventory"] = login("inventory@mailinator.com", TEAM_PASSWORD)
    token["accounts"] = login("accounts@mailinator.com", TEAM_PASSWORD)
    token["am_north_1"] = login(email_for("am_north_1"), TEAM_PASSWORD)

    inventory = token["inventory"]
    print("Signed in as the super admin, both desks and a salesperson.")

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
        "attributes": {"rate": 50000, "unit": "Nos", "instock": 10, "case_size": 1},
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
    desk = api("get", "/fulfilment/procurement", inventory).json()["data"]
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

    api("put", f"/orders/{order['id']}/status", token["am_north_1"], json={"status": "CONFIRMED"})
    api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Paid.",
    })

    check("accounts release it to the warehouse", status_now() == "PAYMENT_VERIFIED", status_now())
    check("the shelf is untouched so far", stock_now() == 12, str(stock_now()))

    # ================================================== picking it
    banner("3. Picking does not move stock")

    api("put", f"/fulfilment/orders/{order['id']}/decide", inventory, json={
        "approve": True, "remarks": "Taken in.",
    })
    check("it goes into procurement", status_now() == "PROCUREMENT", status_now())
    check("the shelf is still untouched", stock_now() == 12, str(stock_now()))

    api("put", f"/fulfilment/orders/{order['id']}/decide", inventory, json={
        "approve": True, "remarks": "Picked and packed.",
    })
    check("it is ready to dispatch", status_now() == "READY", status_now())
    check(
        "a picked order has still not taken the stock",
        stock_now() == 12,
        str(stock_now()),
    )

    # ================================================== dispatching it
    banner("4. Dispatch takes it off the shelf")

    api("put", f"/fulfilment/orders/{order['id']}/decide", inventory, json={
        "approve": True, "remarks": "Gone out.",
    })
    check("it is dispatched", status_now() == "DISPATCHED", status_now())
    check("four have come off the shelf", stock_now() == 8, str(stock_now()))

    desk = api("get", "/fulfilment/procurement", inventory).json()["data"]
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

    api("put", f"/orders/{order['id']}/status", inventory, json={"status": "ON_HOLD", "remarks": "Recalled."})
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

        if created_orders:
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=created_orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)

        session.commit()
        session.close()

        print(
            f"   removed {len(created_orders)} orders, "
            f"{len(created_items)} products and their movements"
        )
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
