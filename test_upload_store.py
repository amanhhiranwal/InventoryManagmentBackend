"""The product upload's Warehouse column: stock lands in that store.

  * a row naming the company's second store puts its stock (and serials)
    there, and a row with no Warehouse uses the company's main store;
  * both are OPENING_BALANCE from PRODUCT_FORM, so the Mongo-to-ledger
    migration does not count them twice;
  * a Warehouse with no Company, another company's store, a store kept for
    other roles, and one serial with a quantity of 5 are refused, and a
    refused sheet writes nothing;
  * the preview names the store; the template lists the stores.

    docker exec -w /app backend_app python test_upload_store.py
"""

import io
import sys
import uuid

from openpyxl import Workbook, load_workbook
from sqlalchemy import text

from app.database.postgres import SessionLocal
from app.services.inventory_service import InventoryService
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login

TAG = uuid.uuid4().hex[:6].upper()
passed, failed = [], []

HEADERS = ["Model Number", "Product Name", "Specification", "Hardware Serial Number", "Product Type Code",
           "Company", "Warehouse", "ECP Rate", "In-Stock Qty", "Case Size", "Unit"]


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


def sql(statement, **params):
    session = SessionLocal()
    try:
        result = session.execute(text(statement), params)
        session.commit()
        return result
    finally:
        session.close()


def scalar(statement, **params):
    session = SessionLocal()
    try:
        return session.execute(text(statement), params).scalar()
    finally:
        session.close()


def sheet(*rows_in) -> bytes:
    book = Workbook()
    page = book.active
    page.title = "Products"
    page.append(HEADERS)
    for row in rows_in:
        page.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def upload(token, content, commit):
    return api("post", "/inventory/items/bulk", token, params={"commit": str(commit).lower()},
               files={"file": (f"stores-{TAG}.xlsx", content)})


admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
company_ids, user_ids = [], []


def company(letter, city):
    r = api("post", "/companies/", admin, json={
        "company_name": f"UPS Test {letter} {TAG}", "company_code": f"UP{letter}{TAG}",
        "email": f"ups.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000071",
        "address_line_1": "Sector 62", "city": city, "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def person(label, permissions, company_list):
    email = f"ups.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "UPS", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-UPS-{label}-{TAG}", "phone_number": "9800000072", "role_ids": [], "company_ids": company_list,
    })
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'ups test', now(), now())",
        i=rid, n=f"UPS Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list})
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD), rid


def held(store_id, sku):
    return float(scalar("select coalesce(sum(qty_on_hand),0) from stock_balance where warehouse_id=:w and sku=:s",
                        w=store_id, s=sku))


try:
    a, b = company("A", "Noida"), company("B", "Delhi")
    name_a, name_b = f"UPS Test A {TAG}", f"UPS Test B {TAG}"
    main = scalar("select id::text from locations where company_id=:c", c=a)
    r = api("post", "/locations/", admin, json={"company_id": a, "location_name": f"Bangalore {TAG}", "location_code": f"UPB{TAG}",
                                               "email": f"ups.blr.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    blr = data(r)["id"]
    loader, _ = person("loader", ["inventory.read", "inventory.create", "inventory.bulk_upload", "stock.read"], [a])

    PANEL, MOUNT = f"UPS-PANEL-{TAG}", f"UPS-MOUNT-{TAG}"
    good = sheet(
        [PANEL, "UPS Panel", "75 inch", f"UPB{TAG}-1/UPB{TAG}-2/UPB{TAG}-3", "IFP", name_a, f"Bangalore {TAG}", 1000, 3, 1, "Pcs"],
        [MOUNT, "UPS Mount", "Tilt", "", "IFP", name_a, "", 50, 7, 1, "Pcs"],
    )

    print("\nPreview")
    r = upload(loader, good, False)
    items = data(r).get("items", []) if r.status_code == 200 else []
    check("the preview names the store for each row", r.status_code == 200 and
          {i["warehouse_name"] for i in items} == {f"Bangalore {TAG}", "Noida Store"}, r.text[:300])
    check("and the preview posts nothing", held(blr, PANEL) == 0 and held(main, MOUNT) == 0)

    print("\nRefused rows write nothing")
    for label, token, row in (
        # For the admin a blank Company is a shared product (for someone
        # with one company it is that company, and the store resolves).
        ("a Warehouse with no Company", admin,
         [f"UPS-X1-{TAG}", "X", "x", "", "IFP", "", f"Bangalore {TAG}", 1, 1, 1, "Pcs"]),
        ("a store that is not one of the company's", loader,
         [f"UPS-X2-{TAG}", "X", "x", "", "IFP", name_a, "Delhi Store", 1, 1, 1, "Pcs"]),
    ):
        r = upload(token, sheet(*[list(x) for x in ([PANEL, "UPS Panel", "75 inch", f"UPB{TAG}-1", "IFP", name_a,
                                                        f"Bangalore {TAG}", 1000, 1, 1, "Pcs"], row)]), True)
        body = data(r)
        check(f"{label} is refused", r.status_code == 200 and not body.get("committed") and body.get("errors"), r.text[:240])
    check("and none of those sheets wrote stock or products",
          held(blr, PANEL) == 0 and InventoryService.items_col.count_documents({"attributes.model_number": PANEL}) == 0)

    print("\nCommit")
    r = upload(loader, good, True)
    body = data(r)
    check("the sheet loads", r.status_code == 200 and body.get("committed") and body.get("stocked") == 10, r.text[:300])
    check("three serialised panels are in the Bangalore store", held(blr, PANEL) == 3)
    check("with their serials", scalar("select count(*) from stock_serial where sku=:s and warehouse_id=:w and status='IN_STOCK'",
                                       s=PANEL, w=blr) == 3)
    check("the row with no Warehouse is in the company's main store", held(main, MOUNT) == 7 and held(blr, MOUNT) == 0)
    check("as opening balance from PRODUCT_FORM, so the migration skips it",
          scalar("select count(distinct source_doc_type) from inventory_ledger where sku in (:p, :m)", p=PANEL, m=MOUNT) == 1
          and scalar("select min(source_doc_type) from inventory_ledger where sku = :p", p=PANEL) == "PRODUCT_FORM")

    LOT = f"UPS-LOT-{TAG}"
    r = upload(loader, sheet([LOT, "UPS Cable", "c", f"UPL{TAG}-0007", "IFP", name_a, "", 5, 24, 1, "Pcs"]), True)
    check("one code with 24 on the shelf still loads, as 24 units of bulk stock in the main store",
          r.status_code == 200 and data(r).get("committed") and held(main, LOT) == 24
          and scalar("select count(*) from stock_serial where sku=:s", s=LOT) == 0, r.text[:240])

    print("\nA store kept for other roles")
    other, other_role = person("other", ["inventory.read"], [a])
    r = api("put", f"/rbac/roles/{other_role}/warehouses", admin, json={"warehouse_ids": [blr]})
    check("Roles & Access keeps Bangalore for another role", r.status_code == 200, r.text[:140])
    r = upload(loader, sheet([f"UPS-K-{TAG}", "K", "k", "", "IFP", name_a, f"Bangalore {TAG}", 1, 2, 1, "Pcs"]), True)
    check("the loader cannot load stock into it", r.status_code == 200 and not data(r).get("committed"), r.text[:200])

    print("\nMany serials, another company")
    import time

    BULK = f"UPS-BULK-{TAG}"
    many = [f"UPM{TAG}-{n:04d}" for n in range(1, 301)]
    started = time.monotonic()
    r = upload(admin, sheet([BULK, "UPS Bulk", "b", "/".join(many), "IFP", name_b, "", 10, 300, 1, "Pcs"]), True)
    took = time.monotonic() - started
    store_b = scalar("select id::text from locations where company_id=:c", c=b)
    check("one row with 300 serials loads 300 products for the other company", r.status_code == 200
          and data(r).get("created") == 300 and InventoryService.items_col.count_documents({"attributes.model_number": BULK}) == 300,
          r.text[:200])
    check("all 300 are in that company's store, by serial", held(store_b, BULK) == 300 and scalar(
        "select count(*) from stock_serial where sku=:s and warehouse_id=:w and status='IN_STOCK'", s=BULK, w=store_b) == 300,
          str(held(store_b, BULK)))
    check(f"in reasonable time ({took:.1f}s)", took < 60, f"{took:.1f}s")

    print("\nTemplate")
    r = api("get", "/inventory/items/bulk/template", loader)
    book = load_workbook(io.BytesIO(r.content))
    heads = [c.value for c in book["Products"][1]]
    listed = [row[7] for row in book["Reference"].iter_rows(min_row=2, values_only=True) if row[7]]
    check("the template has a Warehouse column", "Warehouse" in heads, str(heads))
    check("and lists only the stores the person may use", "Noida Store" in listed and f"Bangalore {TAG}" not in listed, str(listed))

    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger", mismatches == [], str(mismatches))
finally:
    InventoryService.items_col.delete_many({"attributes.model_number": {"$regex": f"^UPS-.*-{TAG}$"}})
    session = SessionLocal()
    for cid in company_ids:
        for table in ("stock_serial", "inventory_ledger", "stock_balance"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid})
    for uid in user_ids:
        roles = [r[0] for r in session.execute(text("select role_id from user_roles where user_id = :u"), {"u": uid})]
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :u"), {"u": uid})
        session.execute(text("delete from users where id = :u"), {"u": uid})
        for rid in roles:
            session.execute(text("delete from role_warehouses where role_id = :r"), {"r": rid})
            session.execute(text("delete from role_permissions where role_id = :r"), {"r": rid})
            session.execute(text("delete from roles where id = :r"), {"r": rid})
    for cid in company_ids:
        session.execute(text("delete from warehouse_bins where warehouse_id in (select id from locations where company_id = :c)"), {"c": cid})
        session.execute(text("delete from locations where company_id = :c"), {"c": cid})
        session.execute(text("delete from user_companies where company_id = :c"), {"c": cid})
        session.execute(text("delete from companies where id = :c"), {"c": cid})
    session.commit()
    session.close()
    print("\nCleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
