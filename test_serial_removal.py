"""Taking serials off a product on the Inventory page's Edit form.

  * removing a serial saves, and In Stock follows the list;
  * removing every serial saves too: the product stays, as one record, at 0;
  * a count that does not match the list is still refused;
  * each removed serial is recorded on Stock Out, under Removed, with who
    removed it - and the figures across the top, which are about orders,
    do not move;
  * correcting a typo (one serial swapped for another) records nothing;
  * a serial added back to a product at 0 goes on that same record;
  * a unit already issued to an order is not touched by the edit;
  * editing still needs inventory.update and a sign-in.

    docker exec -w /app backend_app python test_serial_removal.py
"""

import sys
import uuid

import requests

from app.database.mongodb import sync_mongo_db
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, BASE, TEAM_PASSWORD, api, email_for, login, rows

TAG = uuid.uuid4().hex[:6].upper()
NAME = f"Serial Removal Panel {TAG}"
MODEL = f"SRM-{TAG}"

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


items = sync_mongo_db["inventory_items"]
movements = sync_mongo_db["inventory_movements"]

admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)

types = rows(api("get", "/product-types", admin))
units = rows(api("get", "/inventory/units", admin))
if not types or not units:
    print("Seed the Product Types and Units masters first.")
    sys.exit(1)
kind = types[0]


def payload(serials, instock, **extra):
    return {
        "name": NAME,
        "serial_number": serials[0] if serials else "",
        "serial_numbers": serials,
        "product_type_code": kind["code"],
        "category": kind["category"],
        "attributes": {
            "model_number": MODEL,
            "specification": "Test panel",
            "rate": 1000,
            "unit": str(units[0]),
            "instock": instock,
            "case_size": 1,
        },
        **extra,
    }


def product():
    found = [row for row in rows(api("get", "/inventory/items", admin, params={"search": TAG})) if row.get("name") == NAME]
    return found


def records():
    return list(items.find({"name": NAME}))


def removed_rows():
    body = api("get", "/inventory/stock-out", admin, params={"view": "removed", "search": TAG, "size": 100}).json()
    return body.get("data") or []


def serial(n):
    return f"SRM-{TAG}-{n}"


try:
    created = api("post", "/inventory/items", admin, json={
        **payload([], 3),
        "serial_number": f"{serial(1)} / {serial(2)} / {serial(3)}",
    })
    check("a product with three serials is created", created.status_code == 200, f"{created.status_code} {created.text[:160]}")
    first = product()
    item_id = first[0]["_id"] if first else None
    figures_before = api("get", "/inventory/stock-out", admin).json().get("summary")

    print("\nRemoving one serial")
    r = api("put", f"/inventory/items/{item_id}", admin, json=payload([serial(1), serial(2)], 2))
    check("it saves", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    shown = product()
    check("In Stock is 2", shown and shown[0]["attributes"]["instock"] == 2, str(shown and shown[0]["attributes"]))
    gone = removed_rows()
    check("the removed serial is on Stock Out, under Removed",
          [row["serial_number"] for row in gone] == [serial(3)], str([row["serial_number"] for row in gone]))
    if gone:
        row = gone[0]
        check("with the product, its model, one unit, and no order",
              row["product"] == NAME and row["model_number"] == MODEL and row["quantity"] == 1
              and row["order_id"] is None and row["removed"] is True and row["returned"] is False, str(row))
        check("and who removed it, and when", bool(row["actor"]) and bool(row["created_at"]), str(row))

    print("\nRemoving every serial")
    r = api("put", f"/inventory/items/{item_id}", admin, json=payload([], 1))
    check("a count above an empty list is still refused", r.status_code == 400 and "In stock" in r.text,
          f"{r.status_code} {r.text[:160]}")
    check("and nothing was recorded for it", len(removed_rows()) == 1, str(len(removed_rows())))

    r = api("put", f"/inventory/items/{item_id}", admin, json=payload([], 0))
    check("an empty list at 0 saves", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    shown = product()
    check("the product is still listed, once", len(shown) == 1, str(len(shown)))
    if shown:
        check("at 0 in stock with no serials",
              shown[0]["attributes"]["instock"] == 0 and shown[0]["serial_numbers"] == [], str(shown[0]))
    check("as one record", len(records()) == 1, str(len(records())))
    check("both serials are recorded as removed",
          sorted(row["serial_number"] for row in removed_rows()) == [serial(1), serial(2), serial(3)],
          str([row["serial_number"] for row in removed_rows()]))

    body = api("get", "/inventory/stock-out", admin).json()
    check("the figures across the top do not count removals", body.get("summary") == figures_before,
          f"{body.get('summary')} vs {figures_before}")
    counts = body.get("counts") or {}
    check("the tabs add up, with Removed among them",
          counts.get("all") == counts.get("out", 0) + counts.get("returned", 0) + counts.get("removed", 0)
          and counts.get("removed", 0) >= 3, str(counts))
    out = api("get", "/inventory/stock-out", admin, params={"view": "out", "search": TAG}).json()
    check("the Out tab leaves removals out", out.get("total") == 0, str(out.get("total")))

    print("\nAdding a serial back, and correcting a typo")
    r = api("put", f"/inventory/items/{item_id}", admin, json=payload([serial(4)], 1))
    check("a serial added to a product at 0 saves", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("on the same record", len(records()) == 1 and records()[0]["serial_number"] == serial(4),
          str([(u.get("serial_number"), u["attributes"].get("instock")) for u in records()]))
    check("holding one", records() and records()[0]["attributes"]["instock"] == 1, str(records()))
    r = api("put", f"/inventory/items/{item_id}", admin, json=payload([serial(5)], 1))
    check("swapping one serial for another saves", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("and is a correction, not a removal", len(removed_rows()) == 3, str(len(removed_rows())))

    print("\nA unit already issued to an order")
    template = records()[0]
    issued_id = items.insert_one({
        **{k: v for k, v in template.items() if k != "_id"},
        "serial_number": serial(6),
        "attributes": {**template["attributes"], "instock": 0.0, "stock": 0.0},
        "issued": True,
        "issued_order": "SO-TEST",
    }).inserted_id
    r = api("put", f"/inventory/items/{item_id}", admin, json=payload([serial(5), serial(7)], 2))
    check("an edit beside it saves", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    kept = items.find_one({"_id": issued_id})
    check("the issued unit is untouched", kept is not None and kept.get("serial_number") == serial(6) and kept.get("issued"),
          str(kept))
    check("and not recorded as removed", serial(6) not in [row["serial_number"] for row in removed_rows()])

    print("\nWho may edit")
    r = requests.put(f"{BASE}/inventory/items/{item_id}", json=payload([], 0), timeout=15)
    check("it needs a sign-in", r.status_code in (401, 403), str(r.status_code))
    r = api("put", f"/inventory/items/{item_id}", salesperson, json=payload([], 0))
    check("a salesperson without inventory.update is refused", r.status_code == 403, str(r.status_code))
    check("and nothing was removed", len(removed_rows()) == 3, str(len(removed_rows())))
finally:
    ids = [str(unit["_id"]) for unit in items.find({"name": NAME})]
    movements.delete_many({"$or": [{"item_id": {"$in": ids}}, {"serial_number": {"$regex": f"^SRM-{TAG}-"}}]})
    items.delete_many({"name": NAME})
    print(f"   removed the test product ({len(ids)} record(s)) and its movements")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
