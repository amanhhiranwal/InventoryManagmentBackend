"""The stock ledger, balances and opening stock, end to end through the API.

Two companies, each with a warehouse called Noida, and Company A with a
second warehouse. Products stay in MongoDB; stock moves in PostgreSQL. A
maker prepares opening stock and a different checker approves it. Checks:

  * a draft or submitted document moves no stock; only approval posts it;
  * the maker cannot approve their own document;
  * approving twice, or two approvers at the same moment, posts once;
  * a serial already in stock for the company is refused and nothing of
    that document is posted; another company may use the same serial;
  * products, bins and warehouses of another company are refused;
  * each company and warehouse only sees its own stock;
  * the database itself refuses negative stock;
  * the balances agree with the ledger, and a tampered balance is found.

    docker exec -w /app backend_app python test_stock_ledger.py
"""

import sys
import threading
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.stock import OPENING_BALANCE, UNRESTRICTED
from app.database.postgres import SessionLocal
from app.services.inventory_service import InventoryService
from app.services.stock import posting
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, email_for, login

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
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)

company_ids, location_ids, user_ids = [], [], []
SKU_A = f"TSL-A-{TAG}"
SKU_SHARED = f"TSL-S-{TAG}"
SKU_B = f"TSL-B-{TAG}"


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"Stock Test {letter} {TAG}", "company_code": f"ST{letter}{TAG}",
        "email": f"st.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000031",
        "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    assert r.status_code < 400, r.text
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def warehouse(company_id, code, name="Noida"):
    r = api("post", "/locations/", admin, json={
        "company_id": company_id, "location_name": name, "location_code": f"{code}{TAG}",
        "email": f"wh.{code.lower()}.{TAG.lower()}@mailinator.com", "city": "Noida", "location_type": "WAREHOUSE",
    })
    assert r.status_code < 400, r.text
    location_ids.append(data(r)["id"])
    return data(r)


def bin_of(location_id, code):
    return next(b for b in data(api("get", f"/locations/{location_id}/bins", admin)) if b["code"] == code)


def person(label, permissions, company_list, warehouses):
    email = f"stock.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "Stock", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-ST-{label}-{TAG}", "phone_number": "9800000032", "role_ids": [], "company_ids": company_list,
    })
    assert r.status_code < 400, r.text
    uid = data(r)["id"]
    user_ids.append(uid)
    # One role per person, carrying just the permissions under test.
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'stock test', now(), now())",
        i=rid, n=f"Stock Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p",
            i=str(uuid.uuid4()), r=rid, p=name)
    sql("insert into user_roles (id, user_id, role_id, created_at, updated_at) values (:i, :u, :r, now(), now())",
        i=str(uuid.uuid4()), u=uid, r=rid)
    r = api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list, "warehouse_ids": warehouses})
    assert r.status_code < 400, r.text
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def product(sku, company_id, serial=""):
    InventoryService.items_col.insert_one({
        "name": f"Stock Test Panel {sku}", "serial_number": serial, "product_type_code": "TEST",
        "category": "Test", "company_id": company_id,
        "attributes": {"model_number": sku, "specification": "test", "unit": "Nos", "instock": 0},
        "stock_test_tag": TAG,
    })


def opening(token, warehouse_id, lines, **extra):
    return api("post", "/stock/opening", token, json={"warehouse_id": warehouse_id, "lines": lines, **extra})


def balance(token, **params):
    return data(api("get", "/stock/balances", token, params=params))


def ledger_count(doc_id):
    return scalar("select count(*) from inventory_ledger where source_doc_type = 'STOCK_DOCUMENT' and source_doc_id = :d",
                  d=str(doc_id))


try:
    a, b = company("A"), company("B")
    a1, a2, b1 = warehouse(a, "A1"), warehouse(a, "A2", name="Delhi"), warehouse(b, "B1")
    product(SKU_A, a)
    product(SKU_B, b)
    product(SKU_SHARED, None)

    maker = person("maker", ["stock.read", "stock.opening.create"], [a], [a1["id"]])
    checker = person("checker", ["stock.read", "stock.opening.approve"], [a], [a1["id"]])
    viewer_b = person("viewerb", ["stock.read"], [b], [b1["id"]])
    store_a1, qc_a1, store_b1 = bin_of(a1["id"], "STORE"), bin_of(a1["id"], "QC"), bin_of(b1["id"], "STORE")

    print("\nRefusals while preparing")
    r = opening(maker, a1["id"], [{"sku": SKU_B, "quantity": 1}])
    check("another company's product is refused", r.status_code == 400 and "not in this company" in r.text, r.text[:140])
    r = opening(maker, a1["id"], [{"sku": "NO-SUCH-SKU", "quantity": 1}])
    check("an unknown product is refused", r.status_code == 400, r.text[:120])
    r = opening(maker, a1["id"], [{"sku": SKU_A, "quantity": 1, "bin_id": store_b1["id"]}])
    check("a bin of another warehouse is refused", r.status_code == 400 and "bin" in r.text, r.text[:120])
    r = opening(maker, a1["id"], [{"sku": SKU_A, "quantity": 2, "serials": [f"S{TAG}-1"]}])
    check("serials must match the quantity", r.status_code == 400 and "one per unit" in r.text, r.text[:140])
    r = opening(maker, a1["id"], [{"sku": SKU_A, "quantity": 0}])
    check("a zero quantity is refused", r.status_code == 422, str(r.status_code))
    r = opening(maker, b1["id"], [{"sku": SKU_B, "quantity": 1}])
    check("a warehouse the maker does not work in is not found", r.status_code == 404, str(r.status_code))
    r = opening(viewer_b, b1["id"], [{"sku": SKU_B, "quantity": 1}])
    check("viewing permission alone cannot prepare opening stock", r.status_code == 403, str(r.status_code))
    r = api("get", "/stock/balances", salesperson)
    check("no stock permission, no stock", r.status_code == 403, str(r.status_code))

    print("\nDraft, submit, approve")
    serials = [f"S{TAG}-{i}" for i in range(1, 4)]
    r = opening(maker, a1["id"], [
        {"sku": SKU_A, "quantity": 3, "serials": serials, "unit_cost": 50000},
        {"sku": SKU_SHARED, "quantity": 10, "unit_cost": 120.5},
        {"sku": SKU_SHARED, "quantity": 2, "bin_id": qc_a1["id"]},
    ])
    doc = data(r)
    check("a draft is saved with a company-scoped number", r.status_code == 200 and doc["doc_number"].startswith(f"STA{TAG}-OPN-"),
          r.text[:160])
    check("a draft moves no stock", balance(maker, warehouse_id=a1["id"]) == [] and ledger_count(doc["id"]) == 0)
    r = api("post", f"/stock/opening/{doc['id']}/approve", checker)
    check("a draft cannot be approved before it is submitted", r.status_code == 409, str(r.status_code))
    r = api("post", f"/stock/opening/{doc['id']}/submit", maker)
    check("the maker submits it", r.status_code == 200 and data(r)["status"] == "SUBMITTED")
    check("a submitted document moves no stock either", ledger_count(doc["id"]) == 0)
    r = api("put", f"/stock/opening/{doc['id']}", maker, json={"lines": [{"sku": SKU_A, "quantity": 1}]})
    check("a submitted document can no longer be edited", r.status_code == 409, str(r.status_code))
    sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
        "select :i, ur.role_id, p.id, now(), now() from user_roles ur, permissions p "
        "where ur.user_id = :u and p.permission_name = 'stock.opening.approve'", i=str(uuid.uuid4()), u=user_ids[0])
    r = api("post", f"/stock/opening/{doc['id']}/approve", maker)
    check("the maker cannot approve their own document, even holding the permission", r.status_code == 403 and "other than" in r.text,
          f"{r.status_code} {r.text[:120]}")
    r = api("post", f"/stock/opening/{doc['id']}/approve", checker)
    check("the checker approves and it is posted", r.status_code == 200 and data(r)["status"] == "POSTED", r.text[:160])

    rows_a1 = {(x["sku"], x["bin_code"]): x for x in balance(maker, warehouse_id=a1["id"])}
    check("serial product: 3 on hand in STORE", rows_a1.get((SKU_A, "STORE"), {}).get("qty_on_hand") == 3.0, str(rows_a1))
    check("counted product: 10 in STORE and 2 in QC",
          rows_a1.get((SKU_SHARED, "STORE"), {}).get("qty_on_hand") == 10.0 and rows_a1.get((SKU_SHARED, "QC"), {}).get("qty_on_hand") == 2.0)
    check("available equals on hand with nothing reserved", rows_a1.get((SKU_SHARED, "STORE"), {}).get("qty_available") == 10.0)
    check("one ledger row per serial plus one per counted line", ledger_count(doc["id"]) == 5, str(ledger_count(doc["id"])))
    check("the three serials are on the shelf", scalar(
        "select count(*) from stock_serial where company_id = :c and serial_no = any(:s) and status = 'IN_STOCK'",
        c=a, s=serials) == 3)
    check("unit cost is recorded as the average", rows_a1.get((SKU_A, "STORE"), {}).get("avg_cost") == 50000.0)
    summary = data(api("get", "/stock/summary", maker, params={"warehouse_id": a1["id"]}))
    check("the summary adds up", summary["on_hand"] == 15.0 and summary["available"] == 15.0 and summary["skus"] == 2, str(summary))
    ledger = data(api("get", "/stock/ledger", maker, params={"warehouse_id": a1["id"], "search": doc["doc_number"]}))
    check("the ledger shows who prepared and who approved",
          len(ledger) == 5 and all(x["created_by_name"] and x["approved_by_name"] and x["movement_type"] == "OPENING_BALANCE" for x in ledger))

    print("\nPosting once")
    r = api("post", f"/stock/opening/{doc['id']}/approve", checker)
    check("approving again is refused", r.status_code == 409, str(r.status_code))
    check("and posts nothing more", ledger_count(doc["id"]) == 5)

    doc2 = data(opening(maker, a1["id"], [{"sku": SKU_SHARED, "quantity": 7}]))
    api("post", f"/stock/opening/{doc2['id']}/submit", maker)
    results = []

    def approve():
        results.append(api("post", f"/stock/opening/{doc2['id']}/approve", checker).status_code)

    threads = [threading.Thread(target=approve) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("four approvals at the same moment: exactly one succeeds", sorted(results) == [200, 409, 409, 409], str(results))
    check("and stock moved once", ledger_count(doc2["id"]) == 1 and
          {(x["sku"], x["bin_code"]): x for x in balance(maker, warehouse_id=a1["id"])}[(SKU_SHARED, "STORE")]["qty_on_hand"] == 17.0)

    print("\nSerials")
    doc3 = data(opening(maker, a1["id"], [{"sku": SKU_SHARED, "quantity": 5}, {"sku": SKU_A, "quantity": 1, "serials": [serials[0]]}]))
    api("post", f"/stock/opening/{doc3['id']}/submit", maker)
    r = api("post", f"/stock/opening/{doc3['id']}/approve", checker)
    check("a serial already in stock for the company is refused", r.status_code == 409 and serials[0] in r.text, r.text[:140])
    check("nothing of that document is posted", ledger_count(doc3["id"]) == 0 and
          data(api("get", f"/stock/opening/{doc3['id']}", maker))["status"] == "SUBMITTED")
    check("the earlier quantity is untouched", {(x["sku"], x["bin_code"]): x for x in balance(maker, warehouse_id=a1["id"])}[(SKU_SHARED, "STORE")]["qty_on_hand"] == 17.0)

    print("\nCompany B")
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"B-{TAG}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(b), warehouse_id=uuid.UUID(b1["id"]), bin_id=uuid.UUID(store_b1["id"]),
                                    sku=SKU_SHARED, stock_status=UNRESTRICTED, quantity=Decimal(4), movement_type=OPENING_BALANCE,
                                    source_line_id="1", serials=[serials[0], serials[1], f"B{TAG}-3", f"B{TAG}-4"])],
    ))
    session.commit()
    session.close()
    check("company B may hold the same serial numbers as company A", scalar(
        "select count(*) from stock_serial where company_id = :c and serial_no = any(:s)", c=b, s=serials[:2]) == 2)
    seen_b = balance(viewer_b)
    check("B sees only its own warehouse's stock", {x["warehouse_id"] for x in seen_b} == {b1["id"]} and
          sum(x["qty_on_hand"] for x in seen_b) == 4.0, str(seen_b))
    check("A's maker never sees B's stock", all(x["company_id"] == a for x in balance(maker)))
    r = api("get", "/stock/balances", viewer_b, params={"company_id": a})
    check("B asking for A's company is refused", r.status_code == 403, str(r.status_code))
    r = api("get", "/stock/balances", viewer_b, params={"warehouse_id": a1["id"]})
    check("B asking for A's warehouse is not found", r.status_code == 404, str(r.status_code))
    r = api("get", f"/stock/opening/{doc['id']}", viewer_b)
    check("B cannot open A's document by id", r.status_code == 404, str(r.status_code))
    # A store kept for another role (Roles & Access) is not the maker's,
    # even within their own company.
    kept_for = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'stock test', now(), now())",
        i=kept_for, n=f"Stock Test keeper {TAG}")
    sql("insert into role_warehouses (id, role_id, warehouse_id, created_at, updated_at) values (:i, :r, :w, now(), now())",
        i=str(uuid.uuid4()), r=kept_for, w=a2["id"])
    check("A2 (same company, kept for another role) is not visible to the maker",
          api("get", "/stock/balances", maker, params={"warehouse_id": a2["id"]}).status_code == 404)
    sql("delete from role_warehouses where role_id = :r", r=kept_for)
    sql("delete from roles where id = :r", r=kept_for)
    posting_error = None
    session = SessionLocal()
    try:
        posting.post(session, posting.Posting(
            source_doc_type="TEST", source_doc_id=f"X-{TAG}", source_doc_number=None, movement_date=date.today(),
            movements=[posting.Movement(company_id=uuid.UUID(a), warehouse_id=uuid.UUID(b1["id"]), bin_id=uuid.UUID(store_b1["id"]),
                                        sku=SKU_SHARED, stock_status=UNRESTRICTED, quantity=Decimal(1), movement_type=OPENING_BALANCE,
                                        source_line_id="1")],
        ))
    except Exception as error:  # noqa: BLE001 - the refusal is what is checked
        posting_error = str(getattr(error, "detail", error))
    finally:
        session.rollback()
        session.close()
    check("posting company A stock into company B's warehouse is refused", posting_error and "does not belong" in posting_error, str(posting_error))

    session = SessionLocal()
    try:
        posting.post(session, posting.Posting(
            source_doc_type="TEST", source_doc_id=f"Y-{TAG}", source_doc_number=None, movement_date=date.today(),
            movements=[posting.Movement(company_id=uuid.UUID(b), warehouse_id=uuid.UUID(b1["id"]), bin_id=uuid.UUID(store_b1["id"]),
                                        sku=SKU_SHARED, stock_status=UNRESTRICTED, quantity=Decimal(-9), movement_type="STOCK_ADJUSTMENT_OUT",
                                        source_line_id="1")],
        ))
        posting_error = None
    except Exception as error:  # noqa: BLE001
        posting_error = str(getattr(error, "detail", error))
    finally:
        session.rollback()
        session.close()
    check("taking out more than is on hand is refused", posting_error and "Not enough stock" in posting_error, str(posting_error))

    print("\nThe database's own guards")
    try:
        sql("update stock_balance set qty_on_hand = -1 where company_id = :c", c=b)
        check("the database refuses negative stock", False, "update succeeded")
    except IntegrityError:
        check("the database refuses negative stock", True)
    try:
        sql("insert into inventory_ledger (company_id, warehouse_id, bin_id, sku, movement_type, stock_status, quantity, "
            "source_doc_type, source_doc_id, movement_date, posted_at, idempotency_key) "
            "select company_id, warehouse_id, bin_id, sku, movement_type, stock_status, quantity, source_doc_type, source_doc_id, "
            "movement_date, posted_at, idempotency_key from inventory_ledger where source_doc_id = :d limit 1", d=str(doc["id"]))
        check("the database refuses a second row with the same posting key", False, "insert succeeded")
    except IntegrityError:
        check("the database refuses a second row with the same posting key", True)
    r = api("delete", f"/locations/{a1['id']}", admin)
    check("a warehouse with stock history cannot be deleted", r.status_code == 400 and "stock history" in r.text, f"{r.status_code} {r.text[:120]}")

    print("\nReconciliation")

    def ours(mismatches):
        return [m for m in mismatches if m["company_id"] in company_ids]

    r = api("get", "/stock/reconcile", admin)
    check("balances agree with the ledger", r.status_code == 200 and ours(data(r)) == [], r.text[:200])
    sql("update stock_balance set qty_on_hand = qty_on_hand + 1 where company_id = :c and sku = :s and stock_status = 'UNRESTRICTED' "
        "and bin_id = :b", c=a, s=SKU_SHARED, b=store_a1["id"])
    found = ours(data(api("get", "/stock/reconcile", admin)))
    check("a tampered balance is found", len(found) == 1 and found[0]["ledger_total"] == 17.0 and found[0]["balance_on_hand"] == 18.0, str(found))
    sql("update stock_balance set qty_on_hand = qty_on_hand - 1 where company_id = :c and sku = :s and stock_status = 'UNRESTRICTED' "
        "and bin_id = :b", c=a, s=SKU_SHARED, b=store_a1["id"])
    check("only a super admin can run it", api("get", "/stock/reconcile", maker).status_code == 403)
finally:
    InventoryService.items_col.delete_many({"stock_test_tag": TAG})
    session = SessionLocal()
    for cid in company_ids:
        session.execute(text("delete from stock_serial where company_id = :c"), {"c": cid})
        session.execute(text("delete from inventory_ledger where company_id = :c"), {"c": cid})
        session.execute(text("delete from stock_balance where company_id = :c"), {"c": cid})
        session.execute(text("delete from stock_document where company_id = :c"), {"c": cid})
    for uid in user_ids:
        roles_held = [r[0] for r in session.execute(text("select role_id from user_roles where user_id = :u"), {"u": uid})]
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :u"), {"u": uid})
        session.execute(text("delete from users where id = :u"), {"u": uid})
        for rid in roles_held:
            session.execute(text("delete from role_permissions where role_id = :r"), {"r": rid})
            session.execute(text("delete from roles where id = :r"), {"r": rid})
    for lid in location_ids:
        session.execute(text("delete from locations where id = :l"), {"l": lid})
    for cid in company_ids:
        session.execute(text("delete from user_companies where company_id = :c"), {"c": cid})
        session.execute(text("delete from companies where id = :c"), {"c": cid})
    session.commit()
    left = session.execute(text("select count(*) from inventory_ledger where company_id::text = any(:c)"),
                           {"c": company_ids or [str(uuid.uuid4())]}).scalar()
    session.close()
    print(f"\nCleaned up; ledger rows left behind: {left}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
