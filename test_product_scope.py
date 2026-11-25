"""Roles & Access "Products shown": which products a role sees on Inventory.

  * with no setting a role sees its own companies' products (as before);
  * ALL_COMPANIES shows every company's products to look at, while changing
    another company's product is still refused;
  * STORE_STOCK shows only products in stock at the role's stores - a
    serialised unit by its own serial, so a Bangalore unit is not shown to
    a role that works only in Noida;
  * a person with several roles gets the widest;
  * only a super admin may change it, and only to a known setting.

    docker exec -w /app backend_app python test_product_scope.py
"""

import sys
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.core.stock import OPENING_BALANCE, UNRESTRICTED
from app.database.postgres import SessionLocal
from app.services.inventory_service import InventoryService
from app.services.stock import posting
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
company_ids, user_ids, role_ids = [], [], []


def company(letter, city):
    r = api("post", "/companies/", admin, json={
        "company_name": f"SCP Test {letter} {TAG}", "company_code": f"SC{letter}{TAG}",
        "email": f"scp.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000081",
        "address_line_1": "Sector 62", "city": city, "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def role(label, permissions):
    rid = str(uuid.uuid4())
    role_ids.append(rid)
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'scp test', now(), now())",
        i=rid, n=f"SCP Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    return rid


def person(label, roles, company_list):
    email = f"scp.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "SCP", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-SCP-{label}-{TAG}", "phone_number": "9800000082", "role_ids": [], "company_ids": company_list,
    })
    uid = data(r)["id"]
    user_ids.append(uid)
    api("put", f"/users/{uid}/role", admin, json={"role_ids": roles, "company_ids": company_list})
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def product(company_id, sku, serial=""):
    return str(InventoryService.items_col.insert_one({
        "name": f"SCP {sku}", "serial_number": serial, "product_type_code": "IFP", "category": "IFP", "company_id": company_id,
        "attributes": {"model_number": sku, "specification": "t", "instock": 1, "rate": 10, "unit": "Pcs", "case_size": 1},
        "scp_test_tag": TAG,
    }).inserted_id)


def stock(company_id, store, sku, serials):
    bin_id = scalar("select id::text from warehouse_bins where warehouse_id=:w and code='STORE'", w=store)
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"SCP-{TAG}-{store}-{sku}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(company_id), warehouse_id=uuid.UUID(store), bin_id=uuid.UUID(bin_id),
                                    sku=sku, stock_status=UNRESTRICTED, quantity=Decimal(len(serials) or 1),
                                    movement_type=OPENING_BALANCE, source_line_id="1", serials=serials)]))
    session.commit()
    session.close()


def seen(token):
    r = api("get", "/inventory/items", token, params={"grouped": "false", "search": TAG})
    return {row.get("attributes", {}).get("model_number") + (":" + row["serial_number"] if row.get("serial_number") else "")
            for row in data(r)} if r.status_code == 200 else {f"HTTP {r.status_code}"}


def scope(role_id, value, token=admin):
    return api("put", f"/rbac/roles/{role_id}/product-scope", token, json={"scope": value})


try:
    a, b = company("A", "Noida"), company("B", "Delhi")
    noida = scalar("select id::text from locations where company_id=:c", c=a)
    r = api("post", "/locations/", admin, json={"company_id": a, "location_name": f"Bangalore {TAG}", "location_code": f"SCB{TAG}",
                                               "email": f"scp.blr.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    blr = data(r)["id"]

    PANEL, MOUNT, OTHER = f"SCP-PANEL-{TAG}", f"SCP-MOUNT-{TAG}", f"SCP-OTHER-{TAG}"
    product(a, PANEL, f"SCPN{TAG}")
    product(a, PANEL, f"SCPB{TAG}")
    product(a, MOUNT)
    other_id = product(b, OTHER)
    stock(a, noida, PANEL, [f"SCPN{TAG}"])
    stock(a, blr, PANEL, [f"SCPB{TAG}"])
    stock(a, blr, MOUNT, [])

    reader_role = role("reader", ["inventory.read", "inventory.update"])
    blr_role = role("blr", ["inventory.read"])
    reader = person("reader", [reader_role], [a])

    print("\nOwn companies (no setting)")
    check("a role with no setting reads its own company's products only",
          seen(reader) == {f"{PANEL}:SCPN{TAG}", f"{PANEL}:SCPB{TAG}", MOUNT}, str(seen(reader)))
    check("and the setting reads back as own companies",
          data(api("get", f"/rbac/roles/{reader_role}/product-scope", admin))["scope"] == "OWN_COMPANIES")

    print("\nAll companies")
    check("the admin sets All companies", scope(reader_role, "ALL_COMPANIES").status_code == 200)
    check("the other company's product is now listed", OTHER in seen(reader), str(seen(reader)))
    check("and its picture can be read", api("get", f"/inventory/items/{other_id}/image", reader).status_code in (200, 404))
    r = api("put", f"/inventory/items/{other_id}", reader, json={
        "name": "changed", "serial_number": "", "product_type_code": "IFP", "category": "IFP",
        "attributes": {"model_number": OTHER, "specification": "t", "instock": 1, "rate": 10, "unit": "Pcs", "case_size": 1}})
    check("but changing it is still refused", r.status_code == 403, f"{r.status_code} {r.text[:160]}")

    print("\nStock at the role's stores")
    scope(reader_role, "STORE_STOCK")
    check("with Bangalore open to the company, both units and the mount show",
          seen(reader) == {f"{PANEL}:SCPN{TAG}", f"{PANEL}:SCPB{TAG}", MOUNT}, str(seen(reader)))
    api("put", f"/rbac/roles/{blr_role}/warehouses", admin, json={"warehouse_ids": [blr]})
    check("once Bangalore is kept for another role, only the Noida unit shows",
          seen(reader) == {f"{PANEL}:SCPN{TAG}"}, str(seen(reader)))

    print("\nSeveral roles: the widest wins")
    wide = person("wide", [reader_role, blr_role], [a])
    check("Stock-at-stores plus a role with no setting reads own companies",
          seen(wide) == {f"{PANEL}:SCPN{TAG}", f"{PANEL}:SCPB{TAG}", MOUNT}, str(seen(wide)))

    print("\nWho may change it")
    check("a non-admin cannot change it", scope(reader_role, "ALL_COMPANIES", reader).status_code == 403)
    check("an unknown setting is refused", scope(reader_role, "EVERYTHING").status_code == 422)
finally:
    InventoryService.items_col.delete_many({"scp_test_tag": TAG})
    session = SessionLocal()
    for cid in company_ids:
        for table in ("stock_serial", "inventory_ledger", "stock_balance"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid})
    for uid in user_ids:
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :u"), {"u": uid})
        session.execute(text("delete from users where id = :u"), {"u": uid})
    for rid in role_ids:
        for table in ("role_product_scopes", "role_warehouses", "role_permissions"):
            session.execute(text(f"delete from {table} where role_id = :r"), {"r": rid})
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
