"""Stock adjustments and vendor returns, end to end through the API.

  * an adjustment needs a reason code; out-adjustments cannot take more
    than is on hand; the requester cannot approve their own; above the
    finance limit an approver without Finance's tick is refused;
  * posting writes STOCK_ADJUSTMENT_IN / _OUT once, and moves the balance;
  * a vendor return is against a posted GRN of the same warehouse, takes
    rejected / damaged / accepted goods only up to what that GRN received
    in that state, cannot be claimed twice by two returns, and posts
    VENDOR_RETURN out of the right status once;
  * another company's warehouse is not found.

    docker exec -w /app backend_app python test_adjustments_returns.py
"""

import sys
import threading
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
company_ids, location_ids, user_ids = [], [], []
SKU = f"ADJ-PANEL-{TAG}"


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"Adj Test {letter} {TAG}", "company_code": f"AJ{letter}{TAG}",
        "email": f"adj.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000071",
        "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def warehouse(company_id, code):
    r = api("post", "/locations/", admin, json={"company_id": company_id, "location_name": f"WH {code}", "location_code": f"{code}{TAG}",
                                                "email": f"adj.wh.{code.lower()}.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    location_ids.append(data(r)["id"])
    return data(r)


def person(label, permissions, company_list, warehouses):
    email = f"adj.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "Adj", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-ADJ-{label}-{TAG}", "phone_number": "9800000072", "role_ids": [], "company_ids": company_list,
    })
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'adj test', now(), now())",
        i=rid, n=f"Adj Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list, "warehouse_ids": warehouses})
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def on_hand(company_id, sku, status="UNRESTRICTED"):
    return float(scalar("select coalesce(sum(qty_on_hand),0) from stock_balance where company_id=:c and sku=:s and stock_status=:t",
                        c=company_id, s=sku, t=status))


def rows(doc_id, movement):
    return scalar("select count(*) from inventory_ledger where source_doc_type='STOCK_DOCUMENT' and source_doc_id=:d and movement_type=:m",
                  d=str(doc_id), m=movement)


try:
    a, b = company("A"), company("B")
    wa, wb = warehouse(a, "AJA"), warehouse(b, "AJB")
    InventoryService.items_col.insert_one({"name": f"Adj Panel {TAG}", "serial_number": "", "product_type_code": "TEST", "category": "Test",
                                           "company_id": a, "attributes": {"model_number": SKU, "specification": "t", "instock": 0},
                                           "adj_test_tag": TAG})
    store = next(x for x in data(api("get", f"/locations/{wa['id']}/bins", admin)) if x["code"] == "STORE")
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"ADJ-{TAG}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(a), warehouse_id=uuid.UUID(wa["id"]), bin_id=uuid.UUID(store["id"]), sku=SKU,
                                    stock_status=UNRESTRICTED, quantity=Decimal(10), movement_type=OPENING_BALANCE, source_line_id="1",
                                    unit_cost=Decimal(60000))]))
    session.commit()
    session.close()

    keeper = person("keeper", ["stock.read", "stock.adjust.create", "vendor_return.create"], [a], [wa["id"]])
    manager = person("manager", ["stock.read", "stock.adjust.approve", "vendor_return.approve"], [a], [wa["id"]])
    finance = person("finance", ["stock.read", "stock.adjust.approve", "stock.adjust.finance"], [a], [wa["id"]])

    print("\nAdjustments")
    base = {"warehouse_id": wa["id"], "lines": [{"sku": SKU, "quantity": 1, "unit_cost": 60000}]}
    r = api("post", "/stock/adjustments", keeper, json={**base, "direction": "OUT"})
    check("an adjustment without a reason code is refused", r.status_code == 400 and "why" in r.text, r.text[:120])
    r = api("post", "/stock/adjustments", keeper, json={**base, "direction": "OUT", "reason_code": "DAMAGE", "reason": "Dropped in transit"})
    out = data(r)
    check("a damage write-off is drafted as an out-adjustment", r.status_code == 200 and out["doc_type"] == "ADJUSTMENT_OUT"
          and "-ADJ-" in out["doc_number"], r.text[:140])
    check("a draft moves no stock", on_hand(a, SKU) == 10.0 and rows(out["id"], "STOCK_ADJUSTMENT_OUT") == 0)
    api("post", f"/stock/adjustments/{out['id']}/submit", keeper)
    r = api("post", f"/stock/adjustments/{out['id']}/approve", keeper)
    check("the requester cannot approve (no permission, and not their own)", r.status_code == 403, str(r.status_code))
    results = []

    def approve():
        results.append(api("post", f"/stock/adjustments/{out['id']}/approve", manager).status_code)

    threads = [threading.Thread(target=approve) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("three approvals at once: exactly one posts", sorted(results) == [200, 409, 409], str(results))
    check("one unit written off, once", on_hand(a, SKU) == 9.0 and rows(out["id"], "STOCK_ADJUSTMENT_OUT") == 1)

    r = api("post", "/stock/adjustments", keeper, json={"warehouse_id": wa["id"], "direction": "OUT", "reason_code": "LOSS",
                                                        "lines": [{"sku": SKU, "quantity": 20}]})
    big = data(r)
    api("post", f"/stock/adjustments/{big['id']}/submit", keeper)
    r = api("post", f"/stock/adjustments/{big['id']}/approve", manager)
    check("an out-adjustment cannot take more than is on hand", r.status_code == 409 and "Not enough stock" in r.text, r.text[:140])
    check("and nothing moved", on_hand(a, SKU) == 9.0)

    r = api("post", "/stock/adjustments", keeper, json={"warehouse_id": wa["id"], "direction": "IN", "reason_code": "FOUND",
                                                        "lines": [{"sku": SKU, "quantity": 2, "unit_cost": 60000}]})
    found = data(r)
    api("post", f"/stock/adjustments/{found['id']}/submit", keeper)
    r = api("post", f"/stock/adjustments/{found['id']}/approve", manager)
    check("Rs 1,20,000 found needs Finance: the manager alone is refused", r.status_code == 403 and "finance" in r.text.lower(), r.text[:140])
    r = api("post", f"/stock/adjustments/{found['id']}/approve", finance)
    check("Finance approves it and it posts in", r.status_code == 200 and on_hand(a, SKU) == 11.0
          and rows(found["id"], "STOCK_ADJUSTMENT_IN") == 1, r.text[:140])
    listed = data(api("get", "/stock/adjustments", manager))
    check("the adjustments list shows in and out together", {d["doc_type"] for d in listed} == {"ADJUSTMENT_IN", "ADJUSTMENT_OUT"})

    print("\nVendor returns")
    grn = data(api("post", "/grn/", admin, json={"warehouse_id": wa["id"], "receipt_type": "OTHER", "supplier_name": "Vendor X",
                                                "lines": [{"sku": SKU, "qty_received": 5}]}))
    r = api("post", "/stock/vendor-returns", keeper, json={"warehouse_id": wa["id"], "grn_id": grn["id"],
                                                           "lines": [{"grn_line_id": grn["lines"][0]["id"], "quantity": 1}]})
    check("a GRN that has not been through QC cannot be returned against", r.status_code == 400 and "quality check" in r.text, r.text[:140])
    api("post", f"/grn/{grn['id']}/submit", admin)
    api("post", f"/grn/{grn['id']}/receive", admin)
    api("post", f"/grn/{grn['id']}/inspect", admin, json={"lines": [{"line_id": grn["lines"][0]["id"], "qty_accepted": 2,
                                                                     "qty_rejected": 2, "qty_damaged": 1, "remarks": "Faulty"}]})
    line_id = grn["lines"][0]["id"]
    r = api("post", "/stock/vendor-returns", keeper, json={"warehouse_id": wa["id"], "grn_id": grn["id"],
                                                           "lines": [{"grn_line_id": line_id, "quantity": 3, "stock_status": "REJECTED"}]})
    check("more than the GRN rejected cannot go back", r.status_code == 400 and "only 2" in r.text, r.text[:140])
    r = api("post", "/stock/vendor-returns", keeper, json={"warehouse_id": wa["id"], "grn_id": grn["id"],
                                                           "lines": [{"grn_line_id": line_id, "quantity": 2, "stock_status": "REJECTED"},
                                                                     {"grn_line_id": line_id, "quantity": 1, "stock_status": "BLOCKED"}]})
    ret = data(r)
    check("the rejected and damaged goods are drafted to go back", r.status_code == 200 and ret["doc_type"] == "VENDOR_RETURN", r.text[:140])
    r = api("post", "/stock/vendor-returns", keeper, json={"warehouse_id": wa["id"], "grn_id": grn["id"],
                                                           "lines": [{"grn_line_id": line_id, "quantity": 1, "stock_status": "REJECTED"}]})
    check("a second return cannot claim the same goods", r.status_code == 400 and "only 0" in r.text, r.text[:140])
    api("post", f"/stock/vendor-returns/{ret['id']}/submit", keeper)
    r = api("post", f"/stock/vendor-returns/{ret['id']}/approve", manager)
    check("approved, the goods leave stock as a vendor return", r.status_code == 200 and rows(ret["id"], "VENDOR_RETURN") == 2, r.text[:140])
    check("rejected and blocked are both empty now", on_hand(a, SKU, "REJECTED") == 0 and on_hand(a, SKU, "BLOCKED") == 0)
    check("available stock is untouched by the return", on_hand(a, SKU) == 13.0, str(on_hand(a, SKU)))
    r = api("post", f"/stock/vendor-returns/{ret['id']}/approve", manager)
    check("it cannot be posted twice", r.status_code == 409, str(r.status_code))

    print("\nScope")
    r = api("post", "/stock/adjustments", keeper, json={"warehouse_id": wb["id"], "direction": "IN", "reason_code": "FOUND",
                                                        "lines": [{"sku": SKU, "quantity": 1}]})
    check("another company's warehouse is not found", r.status_code == 404, str(r.status_code))
    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger", mismatches == [], str(mismatches))
finally:
    InventoryService.items_col.delete_many({"adj_test_tag": TAG})
    session = SessionLocal()
    for cid in company_ids:
        for table in ("stock_serial", "inventory_ledger", "stock_balance", "stock_document", "goods_receipt"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid})
        session.execute(text("delete from document_counter where name like :n"), {"n": f"grn:{cid}:%"})
    for uid in user_ids:
        held = [r[0] for r in session.execute(text("select role_id from user_roles where user_id = :u"), {"u": uid})]
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :u"), {"u": uid})
        session.execute(text("delete from users where id = :u"), {"u": uid})
        for rid in held:
            session.execute(text("delete from role_permissions where role_id = :r"), {"r": rid})
            session.execute(text("delete from roles where id = :r"), {"r": rid})
    for lid in location_ids:
        session.execute(text("delete from locations where id = :l"), {"l": lid})
    for cid in company_ids:
        session.execute(text("delete from user_companies where company_id = :c"), {"c": cid})
        session.execute(text("delete from companies where id = :c"), {"c": cid})
    # Number series (PREFIX-TRF_2026 ...) are named after the company code.
    session.execute(text("delete from document_counter where name like :n"), {"n": f"aj%{TAG.lower()}-%"})
    session.commit()
    session.close()
    print("\nCleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
