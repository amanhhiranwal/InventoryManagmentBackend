"""One store per company, and stock added on the Inventory page.

  * creating a company sets up its store (with its main store bin);
  * a person with no particular warehouse grant works in their own
    company's store, and not another company's;
  * a product added with an In-Stock quantity and serials goes straight
    into its company's store, as OPENING_BALANCE from PRODUCT_FORM;
  * the same save retried posts once;
  * another company's store, a serial that is not on the product, a SKU not
    in the catalogue, and a caller without inventory.create are refused.

    docker exec -w /app backend_app python test_product_stock.py
"""

import sys
import uuid

from sqlalchemy import text

from app.database.postgres import SessionLocal
from app.services.inventory_service import InventoryService
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login

TAG = uuid.uuid4().hex[:6].upper()
passed, failed = [], []


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


admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
company_ids, user_ids = [], []
SKU = f"PS-PANEL-{TAG}"
SERIALS = [f"PS{TAG}-1", f"PS{TAG}-2"]


def company(letter, city):
    r = api("post", "/companies/", admin, json={
        "company_name": f"PS Test {letter} {TAG}", "company_code": f"PS{letter}{TAG}",
        "email": f"ps.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000061",
        "address_line_1": "Sector 62", "city": city, "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def person(label, permissions, company_list):
    email = f"ps.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "PS", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-PS-{label}-{TAG}", "phone_number": "9800000062", "role_ids": [], "company_ids": company_list,
    })
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'ps test', now(), now())",
        i=rid, n=f"PS Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list})
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def on_hand(company_id):
    return float(scalar("select coalesce(sum(qty_on_hand),0) from stock_balance where company_id=:c and sku=:s and stock_status='UNRESTRICTED'",
                        c=company_id, s=SKU))


try:
    print("\nOne store per company")
    a, b = company("A", "Noida"), company("B", "Delhi")
    stores = scalar("select count(*) from locations where company_id=:c and location_type='WAREHOUSE'", c=a)
    bins = scalar("select count(*) from warehouse_bins w join locations l on l.id=w.warehouse_id where l.company_id=:c and w.code='STORE'", c=a)
    check("creating a company sets up its store", stores == 1 and bins == 1, f"{stores} store(s), {bins} STORE bin(s)")
    check("named after the company's city", scalar("select location_name from locations where company_id=:c", c=a) == "Noida Store")

    keeper = person("keeper", ["inventory.read", "inventory.create", "stock.read"], [a])
    viewer = person("viewer", ["inventory.read", "stock.read"], [a])
    mine = data(api("get", "/locations/warehouses/mine", keeper))
    check("with no warehouse grant they work in their own company's store", len(mine) == 1 and mine[0]["company_id"] == a, str(mine)[:200])

    print("\nStock added with a product")
    r = api("post", "/inventory/items", keeper, json={
        "name": f"PS Panel {TAG}", "serial_number": "/".join(SERIALS), "product_type_code": "STANDEE", "category": "Standee",
        "attributes": {"rate": 100, "unit": "Pcs", "instock": 2, "case_size": 1, "model_number": SKU, "specification": "t"},
        "company_id": a,
    })
    check("the product saves as before", r.status_code == 200, r.text[:160])
    request_id = str(uuid.uuid4())
    body = {"company_id": a, "sku": SKU, "quantity": 2, "serials": SERIALS, "request_id": request_id}
    r = api("post", "/stock/product-stock", viewer, json=body)
    check("someone who cannot add products is refused", r.status_code == 403, str(r.status_code))
    r = api("post", "/stock/product-stock", keeper, json=body)
    check("its In-Stock goes straight into the company's store", r.status_code == 200 and on_hand(a) == 2, r.text[:160])
    check("as opening balance from the product form, with both serials",
          scalar("select count(*) from inventory_ledger where company_id=:c and sku=:s and source_doc_type='PRODUCT_FORM' "
                 "and movement_type='OPENING_BALANCE' and serial_no is not null", c=a, s=SKU) == 2)
    r = api("post", "/stock/product-stock", keeper, json=body)
    check("the same save retried posts once", r.status_code == 409 and on_hand(a) == 2, f"{r.status_code} {on_hand(a)}")
    r = api("post", "/stock/product-stock", keeper, json={**body, "company_id": b, "request_id": str(uuid.uuid4())})
    check("another company's store is refused", r.status_code in (403, 404), str(r.status_code))
    r = api("post", "/stock/product-stock", keeper, json={**body, "serials": [f"PS{TAG}-X", f"PS{TAG}-Y"], "request_id": str(uuid.uuid4())})
    check("a serial that is not on the product is refused", r.status_code == 400 and "not on" in r.text, r.text[:140])
    r = api("post", "/stock/product-stock", keeper, json={**body, "sku": f"NOPE-{TAG}", "serials": [], "request_id": str(uuid.uuid4())})
    check("a product not in the catalogue is refused", r.status_code == 400, r.text[:140])
    check("and none of that moved anything", on_hand(a) == 2)

    print("\nA person's own store grant")
    store_a = scalar("select id::text from locations where company_id=:c", c=a)
    r = api("put", f"/users/{user_ids[1]}/role", admin, json={
        "role_ids": [scalar("select role_id::text from user_roles where user_id=:u", u=user_ids[1])],
        "company_ids": [a], "warehouse_ids": [store_a]})
    check("Users > Warehouse Access gives the viewer the store", r.status_code == 200, r.text[:140])
    check("and takes nothing from a colleague without a grant",
          store_a in {w["id"] for w in data(api("get", "/locations/warehouses/mine", keeper))})
    api("put", f"/users/{user_ids[1]}/role", admin, json={
        "role_ids": [scalar("select role_id::text from user_roles where user_id=:u", u=user_ids[1])],
        "company_ids": [a], "warehouse_ids": []})

    print("\nSeveral stores, and stores kept for a role")
    r = api("post", "/locations/", admin, json={"company_id": a, "location_name": f"Bengaluru {TAG}", "location_code": f"PSB{TAG}",
                                               "email": f"ps.blr.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    second = data(r)["id"]
    seen = {w["id"] for w in data(api("get", "/locations/warehouses/mine", keeper))}
    check("a company can have a second store, and its people see both", second in seen and len(seen) == 2, str(seen))
    body2 = {"company_id": a, "warehouse_id": second, "sku": SKU, "quantity": 3, "serials": [], "request_id": str(uuid.uuid4())}
    r = api("post", "/stock/product-stock", keeper, json=body2)
    check("stock can go into the chosen store", r.status_code == 200 and r.json()["data"]["store"].startswith("Bengaluru"), r.text[:160])
    check("the same product is then held in both stores, apart",
          float(scalar("select coalesce(sum(qty_on_hand),0) from stock_balance where warehouse_id=:w and sku=:s", w=second, s=SKU)) == 3
          and on_hand(a) == 5)
    store_b = scalar("select id::text from locations where company_id=:c", c=b)
    r = api("post", "/stock/product-stock", admin, json={**body2, "warehouse_id": store_b, "request_id": str(uuid.uuid4())})
    check("a store of another company is refused", r.status_code == 400, r.text[:140])

    roles = {x["role_name"]: x for x in data(api("get", "/roles/", admin))}
    other_role = next(x for name, x in roles.items() if name.startswith("PS Test viewer"))
    r = api("put", f"/rbac/roles/{other_role['id']}/warehouses", admin, json={"warehouse_ids": [second]})
    check("Roles & Access keeps the second store for another role", r.status_code == 200, r.text[:140])
    seen = {w["id"] for w in data(api("get", "/locations/warehouses/mine", keeper))}
    check("so the keeper no longer sees it", second not in seen and len(seen) == 1, str(seen))
    r = api("post", "/stock/product-stock", keeper, json={**body2, "request_id": str(uuid.uuid4())})
    check("and cannot put stock into it", r.status_code == 404, str(r.status_code))
    check("while the viewer, of that role, sees it", second in {w["id"] for w in data(api("get", "/locations/warehouses/mine", viewer))})

    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger", mismatches == [], str(mismatches))
finally:
    InventoryService.items_col.delete_many({"attributes.model_number": SKU})
    session = SessionLocal()
    for cid in company_ids:
        for table in ("stock_serial", "inventory_ledger", "stock_balance"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid})
    for uid in user_ids:
        held = [r[0] for r in session.execute(text("select role_id from user_roles where user_id = :u"), {"u": uid})]
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :u"), {"u": uid})
        session.execute(text("delete from users where id = :u"), {"u": uid})
        for rid in held:
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
