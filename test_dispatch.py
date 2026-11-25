"""Stock out: availability, reservations and dispatch orders, end to end.

Company A holds 6 serial-numbered panels and 10 cables in warehouse A1;
Company B holds panels in B1. A sales order for 6 panels and 4 cables (and
a warranty line, which is not goods). Checks:

  * a sales order moves no stock; its goods lines get stable rows;
  * reserving raises reserved and lowers available, never on hand;
  * reserving more than is available, or in another company's warehouse,
    is refused and changes nothing;
  * a dispatch order cannot take more than is reserved;
  * picking and packing move no stock; a serial cannot be picked twice,
    nor be the wrong product or in another warehouse;
  * commit needs challan, gate pass, vehicle and the e-way bill above the
    limit; it posts SALES_DISPATCH for exactly the dispatched quantity,
    once even when four people press it, consumes the reservation, and
    marks the serials dispatched and the order line dispatched;
  * proof of delivery moves no stock; a dispatched order cannot be
    cancelled; cancelling before dispatch puts picked serials back;
  * releasing a reservation gives the stock back to available;
  * balances agree with the ledger at the end.

    docker exec -w /app backend_app python test_dispatch.py
"""

import sys
import threading
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.core.stock import OPENING_BALANCE, UNRESTRICTED
from app.database.postgres import SessionLocal
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
company_ids, location_ids, user_ids, order_ids = [], [], [], []
PANEL, CABLE = f"DSP-PANEL-{TAG}", f"DSP-CABLE-{TAG}"
SERIALS = [f"DS{TAG}-{i}" for i in range(1, 7)]


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"Dispatch Test {letter} {TAG}", "company_code": f"DT{letter}{TAG}",
        "email": f"dsp.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000051",
        "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    assert r.status_code < 400, r.text
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def warehouse(company_id, code):
    r = api("post", "/locations/", admin, json={
        "company_id": company_id, "location_name": f"Noida {code}", "location_code": f"{code}{TAG}",
        "email": f"dsp.wh.{code.lower()}.{TAG.lower()}@mailinator.com", "city": "Noida", "location_type": "WAREHOUSE",
    })
    assert r.status_code < 400, r.text
    location_ids.append(data(r)["id"])
    return data(r)


def person(label, permissions, company_list, warehouses):
    email = f"dsp.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "Dsp", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-DSP-{label}-{TAG}", "phone_number": "9800000052", "role_ids": [], "company_ids": company_list,
    })
    assert r.status_code < 400, r.text
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'dispatch test', now(), now())",
        i=rid, n=f"Dispatch Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    r = api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list, "warehouse_ids": warehouses})
    assert r.status_code < 400, r.text
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def opening(company_id, warehouse_id, bin_id, sku, qty, serials=()):
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"{TAG}-{sku}-{warehouse_id}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(company_id), warehouse_id=uuid.UUID(warehouse_id), bin_id=uuid.UUID(bin_id),
                                    sku=sku, stock_status=UNRESTRICTED, quantity=Decimal(qty), movement_type=OPENING_BALANCE,
                                    source_line_id="1", serials=list(serials))],
    ))
    session.commit()
    session.close()


def balance(company_id, warehouse_id, sku):
    session = SessionLocal()
    try:
        row = session.execute(text(
            "select coalesce(sum(qty_on_hand),0), coalesce(sum(qty_reserved),0) from stock_balance "
            "where company_id=:c and warehouse_id=:w and sku=:s and stock_status='UNRESTRICTED'"), {"c": company_id, "w": warehouse_id, "s": sku}).one()
        return float(row[0]), float(row[1])
    finally:
        session.close()


def dispatch_rows(do_id):
    return scalar("select count(*) from inventory_ledger where source_doc_type='DISPATCH' and source_doc_id=:d", d=str(do_id))


try:
    a, b = company("A"), company("B")
    a1, b1 = warehouse(a, "DA"), warehouse(b, "DB")
    store_a = next(x for x in data(api("get", f"/locations/{a1['id']}/bins", admin)) if x["code"] == "STORE")
    store_b = next(x for x in data(api("get", f"/locations/{b1['id']}/bins", admin)) if x["code"] == "STORE")
    opening(a, a1["id"], store_a["id"], PANEL, 6, SERIALS)
    opening(a, a1["id"], store_a["id"], CABLE, 10)
    opening(b, b1["id"], store_b["id"], PANEL, 2, [f"DSB{TAG}-1", f"DSB{TAG}-2"])

    clerk = person("clerk", ["dispatch.read", "dispatch.manage", "stock.reserve", "stock.read"], [a], [a1["id"]])
    manager = person("manager", ["dispatch.read", "dispatch.commit"], [a], [a1["id"]])

    session = SessionLocal()
    order_id = session.execute(text(
        "insert into sales_order (customer_name, status, order_number, items, created_at, updated_at) "
        "values (:n, 'PROCUREMENT', :o, cast(:items as json), now(), now()) returning id"),
        {"n": f"Dispatch Customer {TAG}", "o": f"SO-DSP-{TAG}",
         "items": __import__("json").dumps([
             {"product": "Panel", "sku": PANEL, "qty": 6, "quantity_case": 6},
             {"product": "Warranty", "sku": "WARRANTY:4 Years", "qty": 6},
             {"product": "Cable", "sku": CABLE, "qty": 4, "quantity_case": 4},
         ])}).scalar()
    session.commit()
    session.close()
    order_ids.append(order_id)

    print("\nA sales order moves no stock")
    check("on hand is untouched by the order", balance(a, a1["id"], PANEL) == (6.0, 0.0))
    r = api("get", f"/dispatch/orders/{order_id}/availability", clerk, params={"warehouse_id": a1["id"]})
    avail = data(r)
    lines = {x["sku"]: x for x in avail["lines"]}
    check("only goods lines get stable rows (not the warranty)", sorted(lines) == sorted([PANEL, CABLE]), str(list(lines)))
    check("availability shows ordered, available and no shortage", lines[PANEL]["qty_ordered"] == 6 and lines[PANEL]["available_here"] == 6
          and lines[PANEL]["shortage"] == 0, str(lines[PANEL]))
    r = api("get", f"/dispatch/orders/{order_id}/availability", clerk, params={"warehouse_id": b1["id"]})
    check("another company's warehouse is not found", r.status_code == 404, str(r.status_code))
    r = api("get", f"/dispatch/orders/{order_id}/availability", salesperson, params={"warehouse_id": a1["id"]})
    check("without the dispatch permission it is refused", r.status_code == 403, str(r.status_code))

    print("\nReserving")
    panel_line, cable_line = lines[PANEL]["so_line_id"], lines[CABLE]["so_line_id"]

    def reserve(token, wh, qtys):
        return api("post", f"/dispatch/orders/{order_id}/reserve", token,
                   json={"warehouse_id": wh, "lines": [{"so_line_id": k, "qty": v} for k, v in qtys.items()]})

    r = reserve(clerk, a1["id"], {panel_line: 7})
    check("more than the order is refused", r.status_code == 400 and "still to be reserved" in r.text, r.text[:140])
    sql("update stock_balance set qty_reserved = qty_on_hand - 2 where company_id=:c and sku=:s", c=a, s=CABLE)
    r = reserve(clerk, a1["id"], {cable_line: 3})
    check("more than is available is refused", r.status_code == 409 and "Only 2" in r.text, r.text[:140])
    check("and changes nothing", balance(a, a1["id"], CABLE) == (10.0, 8.0))
    sql("update stock_balance set qty_reserved = 0 where company_id=:c and sku=:s", c=a, s=CABLE)
    r = reserve(clerk, a1["id"], {panel_line: 6, cable_line: 4})
    check("the clerk reserves 6 panels and 4 cables", r.status_code == 200, r.text[:160])
    check("on hand stays, reserved goes up", balance(a, a1["id"], PANEL) == (6.0, 6.0) and balance(a, a1["id"], CABLE) == (10.0, 4.0))
    summary = data(api("get", "/stock/summary", clerk, params={"warehouse_id": a1["id"]}))
    check("available drops by what is reserved", summary["available"] == 6.0 and summary["on_hand"] == 16.0, str(summary))
    check("no ledger row is written for a reservation", scalar(
        "select count(*) from inventory_ledger where company_id=:c and movement_type <> 'OPENING_BALANCE'", c=a) == 0)
    check("the order now ships from A1", scalar("select dispatch_warehouse_id::text from sales_order where id=:o", o=order_id) == a1["id"])

    print("\nDispatch order")
    r = api("post", "/dispatch/", clerk, json={"sales_order_id": order_id, "lines": [{"so_line_id": panel_line, "qty": 7}]})
    check("more than is reserved cannot go on a dispatch", r.status_code == 422 or (r.status_code == 400 and "reserved" in r.text), r.text[:140])
    r = api("post", "/dispatch/", clerk, json={"sales_order_id": order_id,
                                               "lines": [{"so_line_id": panel_line, "qty": 4}, {"so_line_id": cable_line, "qty": 3}]})
    first = data(r)
    check("a dispatch order for 4 panels and 3 cables", r.status_code == 200 and first["do_number"].startswith(f"DTA{TAG}-DO-"), r.text[:160])
    r = api("post", "/dispatch/", clerk, json={"sales_order_id": order_id, "lines": [{"so_line_id": panel_line, "qty": 3}]})
    check("a second dispatch cannot claim units the first holds", r.status_code == 400 and "2" in r.text, r.text[:140])

    pl, cl = first["lines"][0]["id"], first["lines"][1]["id"]
    r = api("post", f"/dispatch/{first['id']}/pick", clerk, json={"lines": [{"line_id": pl, "serials": [f"DSB{TAG}-1"] + SERIALS[:3]},
                                                                            {"line_id": cl}]})
    check("a serial from another company's warehouse cannot be picked", r.status_code == 400, r.text[:140])
    r = api("post", f"/dispatch/{first['id']}/pick", clerk, json={"lines": [{"line_id": pl, "serials": SERIALS[:3]}, {"line_id": cl}]})
    check("serials must match the quantity", r.status_code == 400 and "serial(s) for 4" in r.text, r.text[:140])
    r = api("post", f"/dispatch/{first['id']}/pick", clerk, json={"lines": [{"line_id": pl, "serials": SERIALS[:4]}, {"line_id": cl}]})
    check("4 serials are picked", r.status_code == 200 and data(r)["status"] == "PICKED", r.text[:140])
    check("picked serials are held", scalar("select count(*) from stock_serial where company_id=:c and status='PICKED'", c=a) == 4)
    r = api("post", f"/dispatch/{first['id']}/pack", clerk, json={"packages": 2, "gross_weight": "84 kg"})
    check("packed", r.status_code == 200 and data(r)["status"] == "PACKED")
    check("picking and packing move no stock", dispatch_rows(first["id"]) == 0 and balance(a, a1["id"], PANEL) == (6.0, 6.0))

    print("\nCommitting")
    r = api("post", f"/dispatch/{first['id']}/commit", clerk)
    check("the clerk alone cannot commit", r.status_code == 403, str(r.status_code))
    r = api("post", f"/dispatch/{first['id']}/commit", manager)
    # The gate pass number is no longer asked for: it is generated at commit.
    check("commit needs the documents", r.status_code == 400 and "challan" in r.text and "gate pass" not in r.text, r.text[:160])
    api("put", f"/dispatch/{first['id']}/documents", clerk, json={"challan_no": "DC-1", "gate_pass_no": "GP-1", "vehicle_number": "UP16AB1234",
                                                                  "consignment_value": 400000})
    r = api("post", f"/dispatch/{first['id']}/commit", manager)
    check("above the limit it needs an e-way bill", r.status_code == 400 and "e-way bill" in r.text, r.text[:160])
    api("put", f"/dispatch/{first['id']}/documents", clerk, json={"eway_bill_no": "331000123456", "eway_valid_until": "2026-12-31"})
    results = []

    def commit_it():
        results.append(api("post", f"/dispatch/{first['id']}/commit", manager).status_code)

    threads = [threading.Thread(target=commit_it) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("four people committing at once: exactly one succeeds", sorted(results) == [200, 409, 409, 409], str(results))
    check("one ledger row per dispatched serial and one for the cables", dispatch_rows(first["id"]) == 5, str(dispatch_rows(first["id"])))
    check("the ledger takes out exactly what was dispatched", scalar(
        "select sum(quantity) from inventory_ledger where source_doc_type='DISPATCH' and source_doc_id=:d", d=str(first["id"])) == -7)
    check("panels: 2 on hand, 2 still reserved", balance(a, a1["id"], PANEL) == (2.0, 2.0), str(balance(a, a1["id"], PANEL)))
    check("cables: 7 on hand, 1 still reserved", balance(a, a1["id"], CABLE) == (7.0, 1.0), str(balance(a, a1["id"], CABLE)))
    check("the dispatched serials are gone from the shelf", scalar(
        "select count(*) from stock_serial where company_id=:c and status='DISPATCHED'", c=a) == 4)
    avail = {x["sku"]: x for x in data(api("get", f"/dispatch/orders/{order_id}/availability", clerk))["lines"]}
    check("the order shows 4 panels dispatched, 2 to go", avail[PANEL]["qty_dispatched"] == 4 and avail[PANEL]["qty_remaining"] == 2)
    r = api("post", f"/dispatch/{first['id']}/cancel", clerk, json={"reason": "Changed mind"})
    check("a dispatched order cannot be cancelled", r.status_code == 409 and "customer return" in r.text, r.text[:140])

    print("\nGate pass")
    passes = data(api("get", "/gate-passes/", manager, params={"dispatch_order_id": first["id"]}))
    check("committing made one gate pass, keeping the number typed on the dispatch",
          len(passes) == 1 and passes[0]["gate_pass_no"] == "GP-1", str(passes)[:200])
    check("it lists the four panels by serial and the cables", passes and passes[0]["total_qty"] == 7
          and sorted(x["serial"] for x in passes[0]["lines"] if x["serial"]) == sorted(SERIALS[:4]), str(passes)[:300])
    r = api("get", f"/gate-passes/{passes[0]['id']}/pdf", manager) if passes else None
    check("it downloads as a PDF", r is not None and r.status_code == 200 and r.content[:4] == b"%PDF", str(r and r.status_code))
    check("someone without dispatch access cannot open it",
          api("get", f"/gate-passes/{passes[0]['id']}", salesperson).status_code in (403, 404) if passes else False)

    print("\nProof of delivery")
    before = scalar("select count(*) from inventory_ledger where company_id=:c", c=a)
    r = api("post", f"/dispatch/{first['id']}/pod", clerk, json={"receiver": "Store Manager, Lucknow"})
    check("delivery is recorded", r.status_code == 200 and data(r)["status"] == "DELIVERED")
    check("and moves no stock", scalar("select count(*) from inventory_ledger where company_id=:c", c=a) == before
          and balance(a, a1["id"], PANEL) == (2.0, 2.0))
    r = api("post", f"/dispatch/{first['id']}/pod", clerk, json={"receiver": "Again"})
    check("it cannot be recorded twice", r.status_code == 409, str(r.status_code))

    print("\nThe rest, cancelled before dispatch")
    r = api("post", "/dispatch/", clerk, json={"sales_order_id": order_id, "lines": [{"so_line_id": panel_line, "qty": 2}]})
    second = data(r)
    api("post", f"/dispatch/{second['id']}/pick", clerk, json={"lines": [{"line_id": second["lines"][0]["id"], "serials": SERIALS[3:5]}]})
    check("a serial already dispatched cannot be picked again", scalar(
        "select count(*) from stock_serial where company_id=:c and status='PICKED'", c=a) == 0)
    r = api("post", f"/dispatch/{second['id']}/pick", clerk, json={"lines": [{"line_id": second["lines"][0]["id"], "serials": SERIALS[4:6]}]})
    check("the last two panels are picked", r.status_code == 200, r.text[:140])
    r = api("post", f"/dispatch/{second['id']}/cancel", clerk, json={"reason": "Customer postponed"})
    check("cancelling before dispatch is allowed", r.status_code == 200 and data(r)["status"] == "CANCELLED")
    check("and puts the picked serials back on the shelf", scalar(
        "select count(*) from stock_serial where company_id=:c and status='IN_STOCK'", c=a) == 2)
    check("the reservation stays for the order", balance(a, a1["id"], PANEL) == (2.0, 2.0))
    r = api("post", f"/dispatch/orders/{order_id}/release", clerk, json={"reason": "Order on hold"})
    check("releasing gives the stock back to available", r.status_code == 200 and balance(a, a1["id"], PANEL) == (2.0, 0.0)
          and balance(a, a1["id"], CABLE) == (7.0, 0.0), r.text[:160])

    print("\nScope and books")
    listed = api("get", "/dispatch/", manager).json()
    check("the list counts by status", listed["counts"].get("DELIVERED") == 1 and listed["counts"].get("CANCELLED") == 1, str(listed["counts"]))
    r = api("get", f"/dispatch/{first['id']}", salesperson)
    check("no dispatch permission, no dispatch order", r.status_code == 403, str(r.status_code))
    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger", mismatches == [], str(mismatches))
    check("company B's panels were never touched", balance(b, b1["id"], PANEL) == (2.0, 0.0))
finally:
    session = SessionLocal()
    for oid in order_ids:
        session.execute(text("delete from gate_pass where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from dispatch_order where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from stock_reservation where so_line_id in (select id from sales_order_line where sales_order_id=:o)"), {"o": oid})
        session.execute(text("delete from sales_order_line where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order where id=:o"), {"o": oid})
    for cid in company_ids:
        session.execute(text("delete from stock_serial where company_id=:c"), {"c": cid})
        session.execute(text("delete from inventory_ledger where company_id=:c"), {"c": cid})
        session.execute(text("delete from stock_balance where company_id=:c"), {"c": cid})
    for uid in user_ids:
        roles_held = [r[0] for r in session.execute(text("select role_id from user_roles where user_id=:u"), {"u": uid})]
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id=:u"), {"u": uid})
        session.execute(text("delete from users where id=:u"), {"u": uid})
        for rid in roles_held:
            session.execute(text("delete from role_permissions where role_id=:r"), {"r": rid})
            session.execute(text("delete from roles where id=:r"), {"r": rid})
    for lid in location_ids:
        session.execute(text("delete from locations where id=:l"), {"l": lid})
    for cid in company_ids:
        session.execute(text("delete from user_companies where company_id=:c"), {"c": cid})
        session.execute(text("delete from companies where id=:c"), {"c": cid})
    session.commit()
    session.close()
    print("\nCleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
