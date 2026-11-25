"""Warehouse transfers, cycle counts and customer returns, through the API.

Transfers
  * a transfer to another company's warehouse is refused (that is a sale);
  * a draft, a submitted and an approved transfer move no stock; the
    requester cannot approve their own;
  * shipping posts TRANSFER_OUT once: the sender's stock falls and the
    same quantity is IN_TRANSIT at the receiver; reserved stock cannot ship;
    serial-numbered units travel with their serials;
  * receiving moves in transit to available stock, damaged to BLOCKED, and
    writes off the shortfall; it needs a remark for shortage or damage, a
    user of the receiving warehouse, and happens once;
  * a shipped transfer cannot be cancelled.

Cycle counts
  * starting a count snapshots available store stock; nothing moves until
    it is approved by someone other than the counter;
  * approval posts each difference as STOCK_ADJUSTMENT_IN / _OUT once;
  * approval is refused if stock moved since the count started.

Customer returns
  * a return GRN against a dispatch takes back only what went out on that
    dispatch, less earlier returns; a dispatch of another company is refused;
  * the goods enter QUARANTINE, never available stock directly.

Ledger and balances agree at the end.

    docker exec -w /app backend_app python test_transfers_counts.py
"""

import json
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
company_ids, location_ids, user_ids, order_ids = [], [], [], []
SKU = f"TRF-PANEL-{TAG}"
SER = f"TRF-CAM-{TAG}"
SERIALS = [f"TC{TAG}-{i}" for i in range(1, 5)]


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"Trf Test {letter} {TAG}", "company_code": f"TF{letter}{TAG}",
        "email": f"trf.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000081",
        "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def warehouse(company_id, code):
    r = api("post", "/locations/", admin, json={"company_id": company_id, "location_name": f"WH {code}", "location_code": f"{code}{TAG}",
                                                "email": f"trf.wh.{code.lower()}.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    location_ids.append(data(r)["id"])
    return data(r)


def bins_of(wh):
    return {x["code"]: x for x in data(api("get", f"/locations/{wh['id']}/bins", admin))}


def person(label, permissions, company_list, warehouses):
    email = f"trf.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "Trf", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-TRF-{label}-{TAG}", "phone_number": "9800000082", "role_ids": [], "company_ids": company_list,
    })
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'trf test', now(), now())",
        i=rid, n=f"Trf Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list, "warehouse_ids": warehouses})
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def qty(warehouse_id, sku, status="UNRESTRICTED"):
    return float(scalar("select coalesce(sum(qty_on_hand),0) from stock_balance where warehouse_id=:w and sku=:s and stock_status=:t",
                        w=warehouse_id, s=sku, t=status))


def ledger(doc_type, doc_id, movement=None):
    return scalar("select count(*) from inventory_ledger where source_doc_type=:t and source_doc_id=:d"
                  + (" and movement_type=:m" if movement else ""), t=doc_type, d=str(doc_id), m=movement)


def opening(company_id, wh, bin_id, sku, quantity, serials=()):
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"TRF-{TAG}-{sku}-{uuid.uuid4().hex[:6]}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(company_id), warehouse_id=uuid.UUID(wh["id"]), bin_id=uuid.UUID(bin_id),
                                    sku=sku, stock_status=UNRESTRICTED, quantity=Decimal(quantity), movement_type=OPENING_BALANCE,
                                    source_line_id="1", unit_cost=Decimal(1000), serials=list(serials))]))
    session.commit()
    session.close()


try:
    a, b = company("A"), company("B")
    a1, a2, b1 = warehouse(a, "TA1"), warehouse(a, "TA2"), warehouse(b, "TB1")
    for sku, name, serial in ((SKU, "Trf Panel", ""), (SER, "Trf Camera", "S-1")):
        InventoryService.items_col.insert_one({"name": f"{name} {TAG}", "serial_number": serial, "product_type_code": "TEST",
                                               "category": "Test", "company_id": a,
                                               "attributes": {"model_number": sku, "specification": "t", "instock": 0},
                                               "trf_test_tag": TAG})
    a1_bins, a2_bins = bins_of(a1), bins_of(a2)
    opening(a, a1, a1_bins["STORE"]["id"], SKU, 10)
    opening(a, a1, a1_bins["STORE"]["id"], SER, 4, SERIALS)

    requester = person("requester", ["stock.read", "transfer.create", "transfer.ship"], [a], [a1["id"]])
    approver = person("approver", ["stock.read", "transfer.approve", "stock.count.approve"], [a], [a1["id"], a2["id"]])
    receiver = person("receiver", ["stock.read", "transfer.receive", "stock.count.create"], [a], [a2["id"]])
    counter = person("counter", ["stock.read", "stock.count.create"], [a], [a1["id"]])

    # ------------------------------------------------------------------
    print("\nTransfers")
    r = api("post", "/stock/transfers", requester, json={"from_warehouse_id": a1["id"], "to_warehouse_id": b1["id"],
                                                         "lines": [{"sku": SKU, "quantity": 1}]})
    check("a transfer to another company's warehouse is refused", r.status_code == 400 and "another company" in r.text, r.text[:140])
    r = api("post", "/stock/transfers", requester, json={"from_warehouse_id": a1["id"], "to_warehouse_id": a1["id"],
                                                         "lines": [{"sku": SKU, "quantity": 1}]})
    check("a transfer to the same warehouse is refused", r.status_code == 400, r.text[:120])

    r = api("post", "/stock/transfers", requester, json={
        "from_warehouse_id": a1["id"], "to_warehouse_id": a2["id"], "reason": "Rebalance",
        "lines": [{"sku": SKU, "quantity": 6}, {"sku": SER, "quantity": 3, "serials": SERIALS[:3]}]})
    trf = data(r)
    check("a same-company transfer is drafted", r.status_code == 200 and "-TRF-" in trf["transfer_number"], r.text[:140])
    tid = trf["id"]
    api("post", f"/stock/transfers/{tid}/submit", requester)
    r = api("post", f"/stock/transfers/{tid}/ship", requester, json={"vehicle_number": "UP16AB1"})
    check("it cannot ship before approval", r.status_code == 409, r.text[:120])
    check("draft and submitted moved no stock", qty(a1["id"], SKU) == 10 and ledger("STOCK_TRANSFER", tid) == 0)
    r = api("post", f"/stock/transfers/{tid}/approve", requester)
    check("the requester cannot approve", r.status_code == 403, str(r.status_code))
    r = api("post", f"/stock/transfers/{tid}/approve", approver)
    check("another person approves; still no stock moves", r.status_code == 200 and qty(a1["id"], SKU) == 10, r.text[:120])

    # Reserve 5 of the 10 so only 5 may leave: the transfer wants 6.
    sql("update stock_balance set qty_reserved = 5 where warehouse_id=:w and sku=:s and stock_status='UNRESTRICTED'", w=a1["id"], s=SKU)
    r = api("post", f"/stock/transfers/{tid}/ship", requester, json={"vehicle_number": "UP16AB1"})
    check("reserved stock cannot be shipped", r.status_code == 409 and "reserved" in r.text, r.text[:140])
    sql("update stock_balance set qty_reserved = 0 where warehouse_id=:w and sku=:s and stock_status='UNRESTRICTED'", w=a1["id"], s=SKU)
    r = api("post", f"/stock/transfers/{tid}/ship", requester, json={})
    check("shipping needs a vehicle or docket number", r.status_code == 400, r.text[:120])

    results = []

    def ship():
        results.append(api("post", f"/stock/transfers/{tid}/ship", requester, json={"vehicle_number": "UP16AB1"}).status_code)

    threads = [threading.Thread(target=ship) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("three ships at once: exactly one goes", sorted(results) == [200, 409, 409], str(results))
    check("the sender's stock fell by what shipped", qty(a1["id"], SKU) == 4 and qty(a1["id"], SER) == 1)
    check("the same quantity is in transit at the receiver", qty(a2["id"], SKU, "IN_TRANSIT") == 6 and qty(a2["id"], SER, "IN_TRANSIT") == 3)
    check("TRANSFER_OUT written once per side", ledger("STOCK_TRANSFER", tid, "TRANSFER_OUT") == 2 + 6, str(ledger("STOCK_TRANSFER", tid)))
    check("the shipped serials are in transit",
          scalar("select count(*) from stock_serial where serial_no = any(:s) and status='IN_TRANSIT' and warehouse_id=:w",
                 s=SERIALS[:3], w=a2["id"]) == 3)
    r = api("post", f"/stock/transfers/{tid}/cancel", requester, json={"reason": "Changed mind"})
    check("a shipped transfer cannot be cancelled", r.status_code == 409, r.text[:120])

    shipped = data(api("get", f"/stock/transfers/{tid}", receiver))
    panel_line = next(x for x in shipped["lines"] if x["sku"] == SKU)["id"]
    cam_line = next(x for x in shipped["lines"] if x["sku"] == SER)["id"]
    r = api("post", f"/stock/transfers/{tid}/receive", requester,
            json={"lines": [{"line_id": panel_line, "qty_received": 6}, {"line_id": cam_line, "qty_received": 3, "serials_received": SERIALS[:3]}]})
    check("the sender cannot receive (no permission at the receiving end)", r.status_code in (403, 404), str(r.status_code))
    r = api("post", f"/stock/transfers/{tid}/receive", receiver,
            json={"lines": [{"line_id": panel_line, "qty_received": 4, "qty_damaged": 1},
                            {"line_id": cam_line, "qty_received": 3, "serials_received": SERIALS[:3]}]})
    check("shortage or damage needs a remark", r.status_code == 400 and "say what happened" in r.text, r.text[:140])
    r = api("post", f"/stock/transfers/{tid}/receive", receiver,
            json={"lines": [{"line_id": panel_line, "qty_received": 4, "qty_damaged": 1, "remarks": "1 cracked, 1 missing"},
                            {"line_id": cam_line, "qty_received": 2, "serials_received": SERIALS[:2]}]})
    check("every serial received has to be named", r.status_code == 400, r.text[:140])
    r = api("post", f"/stock/transfers/{tid}/receive", receiver,
            json={"lines": [{"line_id": panel_line, "qty_received": 4, "qty_damaged": 1, "remarks": "1 cracked, 1 missing"},
                            {"line_id": cam_line, "qty_received": 3, "serials_received": SERIALS[:3]}]})
    got = data(r)
    check("received with a discrepancy", r.status_code == 200 and got["status"] == "RECEIVED"
          and next(x for x in got["lines"] if x["sku"] == SKU)["qty_short"] == 1, r.text[:160])
    check("in transit is empty", qty(a2["id"], SKU, "IN_TRANSIT") == 0 and qty(a2["id"], SER, "IN_TRANSIT") == 0)
    check("good units are available, damaged blocked",
          qty(a2["id"], SKU) == 4 and qty(a2["id"], SKU, "BLOCKED") == 1 and qty(a2["id"], SER) == 3)
    check("the shortfall is written off",
          ledger("STOCK_TRANSFER_IN", tid, "STOCK_ADJUSTMENT_OUT") == 1, str(ledger("STOCK_TRANSFER_IN", tid, "STOCK_ADJUSTMENT_OUT")))
    check("the serials are on the receiver's shelf",
          scalar("select count(*) from stock_serial where serial_no = any(:s) and status='IN_STOCK' and warehouse_id=:w",
                 s=SERIALS[:3], w=a2["id"]) == 3)
    r = api("post", f"/stock/transfers/{tid}/receive", receiver,
            json={"lines": [{"line_id": panel_line, "qty_received": 6}, {"line_id": cam_line, "qty_received": 3, "serials_received": SERIALS[:3]}]})
    check("it cannot be received twice", r.status_code == 409, str(r.status_code))
    check("the receiver sees it; the counter (sender side only) does too",
          api("get", f"/stock/transfers/{tid}", receiver).status_code == 200 and api("get", f"/stock/transfers/{tid}", counter).status_code == 200)
    outsider = person("outsider", ["stock.read"], [b], [b1["id"]])
    check("someone of another company does not find it", api("get", f"/stock/transfers/{tid}", outsider).status_code == 404)

    print("\nReview fixes")
    r = api("post", "/stock/transfers", requester, json={"from_warehouse_id": a1["id"], "to_warehouse_id": a2["id"],
                                                         "lines": [{"sku": SER, "quantity": 1}]})
    check("serial-numbered goods cannot be transferred without naming serials", r.status_code == 400 and "serial" in r.text, r.text[:140])
    r = api("post", "/stock/transfers", requester, json={"from_warehouse_id": a1["id"], "to_warehouse_id": a2["id"],
                                                         "lines": [{"sku": SKU, "quantity": 1}]})
    declined = data(r)["id"]
    r = api("post", f"/stock/transfers/{declined}/cancel", approver, json={"reason": "Not needed"})
    check("an approver cannot cancel a draft", r.status_code == 403, str(r.status_code))
    api("post", f"/stock/transfers/{declined}/submit", requester)
    r = api("post", f"/stock/transfers/{declined}/cancel", approver, json={"reason": "Not needed this month"})
    check("an approver can decline a submitted transfer", r.status_code == 200 and data(r)["status"] == "CANCELLED", r.text[:140])
    r = api("put", f"/locations/{a1['id']}", admin, json={"location_type": "OFFICE"})
    check("a warehouse with stock cannot become an office", r.status_code == 400 and "stock history" in r.text, r.text[:140])

    # ------------------------------------------------------------------
    print("\nCycle counts")
    r = api("post", "/stock/counts", counter, json={"warehouse_id": a1["id"]})
    cnt = data(r)
    lines = {x["sku"]: x for x in cnt["lines"]}
    check("starting a count snapshots the shelf", r.status_code == 200 and lines.get(SKU, {}).get("qty_system") == 4
          and lines.get(SER, {}).get("qty_system") == 1, r.text[:160])
    cid = cnt["id"]
    r = api("post", f"/stock/counts/{cid}/submit", counter)
    check("it cannot be submitted half counted", r.status_code == 400, r.text[:120])
    r = api("put", f"/stock/counts/{cid}", counter, json={"lines": [{"line_id": lines[SKU]["id"], "qty_counted": 3, "remarks": "One short"},
                                                                    {"line_id": lines[SER]["id"], "qty_counted": 1}]})
    check("counts are recorded and the difference shown", r.status_code == 200 and data(r)["lines_with_variance"] == 1, r.text[:140])
    api("post", f"/stock/counts/{cid}/submit", counter)
    check("a submitted count moves no stock", qty(a1["id"], SKU) == 4 and ledger("STOCK_COUNT", cid) == 0)
    r = api("post", f"/stock/counts/{cid}/approve", counter)
    check("the counter cannot approve", r.status_code == 403, str(r.status_code))

    # Something moves after the count started: approval must refuse.
    opening(a, a1, a1_bins["STORE"]["id"], SKU, 1)
    r = api("post", f"/stock/counts/{cid}/approve", approver)
    check("stock moved since the count: approval refused", r.status_code == 409 and "moved" in r.text, r.text[:140])
    api("post", f"/stock/counts/{cid}/cancel", counter, json={"reason": "Stock moved"})

    r = api("post", "/stock/counts", counter, json={"warehouse_id": a1["id"], "bin_id": a1_bins["STORE"]["id"]})
    cnt = data(r)
    lines = {x["sku"]: x for x in cnt["lines"]}
    cid = cnt["id"]
    api("put", f"/stock/counts/{cid}", counter, json={"lines": [{"line_id": lines[SKU]["id"], "qty_counted": 3},
                                                                {"line_id": lines[SER]["id"], "qty_counted": 1}]})
    api("post", f"/stock/counts/{cid}/submit", counter)
    results = []

    def approve():
        results.append(api("post", f"/stock/counts/{cid}/approve", approver).status_code)

    threads = [threading.Thread(target=approve) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("three approvals at once: exactly one posts", sorted(results) == [200, 409, 409], str(results))
    check("the difference is posted as an adjustment out, once",
          qty(a1["id"], SKU) == 3 and ledger("STOCK_COUNT", cid, "STOCK_ADJUSTMENT_OUT") == 1, str(qty(a1["id"], SKU)))

    r = api("post", "/stock/counts", receiver, json={"warehouse_id": a2["id"]})
    c2 = data(r)
    l2 = {x["sku"]: x for x in c2["lines"]}
    api("put", f"/stock/counts/{c2['id']}", receiver, json={"lines": [{"line_id": l2[SKU]["id"], "qty_counted": 4},
                                                                      {"line_id": l2[SER]["id"], "qty_counted": 2}]})
    api("post", f"/stock/counts/{c2['id']}/submit", receiver)
    r = api("post", f"/stock/counts/{c2['id']}/approve", approver)
    check("a difference on a serial-numbered product is refused", r.status_code == 409 and "serial" in r.text, r.text[:140])

    # ------------------------------------------------------------------
    print("\nCustomer returns")
    session = SessionLocal()
    order_id = session.execute(text(
        "insert into sales_order (customer_name, company_name, status, order_number, items, created_at, updated_at) "
        "values (:n, :c, 'DELIVERED', :o, cast(:items as json), now(), now()) returning id"),
        {"n": f"Return Customer {TAG}", "c": f"Return Co {TAG}", "o": f"SO-TRF-{TAG}",
         "items": json.dumps([{"product": "Panel", "sku": SKU, "qty": 3}])}).scalar()
    order_ids.append(order_id)
    so_line = session.execute(text(
        "insert into sales_order_line (sales_order_id, line_index, sku, product_name, qty_ordered, qty_dispatched, qty_delivered) "
        "values (:o, 0, :s, 'Panel', 3, 3, 3) returning id"), {"o": order_id, "s": SKU}).scalar()
    do_id = session.execute(text(
        "insert into dispatch_order (do_number, company_id, warehouse_id, sales_order_id, status, created_at, updated_at) "
        "values (:n, :c, :w, :o, 'DELIVERED', now(), now()) returning id"),
        {"n": f"DO-TRF-{TAG}", "c": a, "w": a1["id"], "o": order_id}).scalar()
    do_line = session.execute(text(
        "insert into dispatch_line (dispatch_id, so_line_id, sku, product_name, qty, qty_picked) values (:d, :l, :s, 'Panel', 3, 3) returning id"),
        {"d": do_id, "l": so_line, "s": SKU}).scalar()
    session.commit()
    session.close()

    grn_maker = person("grn", ["grn.read", "grn.create", "grn.post"], [a], [a1["id"], a2["id"]])
    found = data(api("get", "/grn/dispatches", grn_maker, params={"warehouse_id": a2["id"]}))
    check("the dispatch is offered for returns, with what can come back",
          any(d["id"] == do_id and d["lines"][0]["qty_returnable"] == 3 for d in found), str(found)[:160])
    base = {"warehouse_id": a2["id"], "receipt_type": "RETURN", "dispatch_order_id": do_id}
    r = api("post", "/grn/", grn_maker, json={**base, "lines": [{"sku": SKU, "dispatch_line_id": do_line, "qty_received": 4}]})
    check("more than went out cannot come back", r.status_code == 400 and "only 3" in r.text, r.text[:140])
    r = api("post", "/grn/", grn_maker, json={**base, "lines": [{"sku": SKU, "qty_received": 1}]})
    check("each line has to name the dispatch line", r.status_code == 400, r.text[:120])
    r = api("post", "/grn/", grn_maker, json={**base, "lines": [{"sku": SKU, "dispatch_line_id": do_line, "qty_received": 2}]})
    ret = data(r)
    check("a return of 2 is drafted, from the customer", r.status_code == 200 and ret["do_number"] == f"DO-TRF-{TAG}"
          and ret["supplier_name"] == f"Return Co {TAG}", r.text[:160])
    r = api("post", "/grn/", grn_maker, json={**base, "lines": [{"sku": SKU, "dispatch_line_id": do_line, "qty_received": 2}]})
    check("a second return cannot take the same goods", r.status_code == 400 and "only 1" in r.text, r.text[:140])
    before = qty(a2["id"], SKU)
    api("post", f"/grn/{ret['id']}/submit", grn_maker)
    r = api("post", f"/grn/{ret['id']}/receive", grn_maker)
    check("returned goods enter quarantine, not available stock",
          r.status_code == 200 and qty(a2["id"], SKU, "QUARANTINE") == 2 and qty(a2["id"], SKU) == before, r.text[:140])
    check("posted as CUSTOMER_RETURN", ledger("GRN", ret["id"], "CUSTOMER_RETURN") == 1)
    r = api("post", "/grn/", grn_maker, json={**{**base, "warehouse_id": b1["id"]},
                                               "lines": [{"sku": SKU, "dispatch_line_id": do_line, "qty_received": 1}]})
    check("another company's warehouse cannot take it back", r.status_code in (400, 404), str(r.status_code))

    print("\nLedger")
    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger", mismatches == [], str(mismatches))
finally:
    InventoryService.items_col.delete_many({"trf_test_tag": TAG})
    session = SessionLocal()
    for cid_ in company_ids:
        for table in ("goods_receipt",):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid_})
    for oid in order_ids:
        session.execute(text("delete from gate_pass where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from dispatch_order where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order_line where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order where id=:o"), {"o": oid})
    for cid_ in company_ids:
        for table in ("stock_serial", "inventory_ledger", "stock_balance", "stock_transfer", "stock_count", "stock_document"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid_})
        session.execute(text("delete from document_counter where name like :n"), {"n": f"grn:{cid_}:%"})
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
    for cid_ in company_ids:
        session.execute(text("delete from user_companies where company_id = :c"), {"c": cid_})
        session.execute(text("delete from companies where id = :c"), {"c": cid_})
    # Number series (PREFIX-TRF_2026 ...) are named after the company code.
    session.execute(text("delete from document_counter where name like :n"), {"n": f"tf%{TAG.lower()}-%"})
    session.commit()
    session.close()
    print("\nCleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
