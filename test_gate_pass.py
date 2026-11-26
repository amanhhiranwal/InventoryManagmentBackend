"""Gate passes made when Logistics sends an order out, and their numbers.

  * marking an order out for delivery makes its gate pass, numbered per
    company per financial year (2026-27/001, then 002), listing each issued
    unit by serial and the Bill To / Ship To from the order;
  * doing it again (or asking again) gives the same pass, not a new one;
  * an order with no company still goes out, says why it has no pass, and
    making one by hand is refused with the reason;
  * another company's numbers run on their own;
  * a dispatch with no typed number gets the next number written onto it;
  * only logistics make one by hand; someone not on the order's line or a
    desk cannot read it; the PDF downloads.

    docker exec -w /app backend_app python test_gate_pass.py
"""

import json
import sys
import uuid

from sqlalchemy import text

from app.core.timezone import now_ist
from app.database.postgres import SessionLocal
from app.services.stock.gate_pass import financial_year
from app.services.stock_movement_service import movements_col
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, email_for, login

TAG = uuid.uuid4().hex[:6].upper()
passed, failed = [], []
YEAR = financial_year(now_ist().date())


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


def scalar(statement, **params):
    session = SessionLocal()
    try:
        value = session.execute(text(statement), params).scalar()
        session.commit()
        return value
    finally:
        session.close()


admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
company_ids, order_ids = [], []
# The orders' owner: the (mailinator) admin, outside the salesperson's line.
OWNER = scalar("select id::text from users where email = :e", e=ADMIN_EMAIL)


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"GP Test {letter} {TAG}", "company_code": f"GP{letter}{TAG}",
        "email": f"gp.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000091",
        "address_line_1": "B-66 Sector 65", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def order(label, company_id, serials):
    number = f"SO-GP-{label}-{TAG}"
    oid = scalar(
        "insert into sales_order (customer_name, company_name, status, order_number, items, billing_address, shipping_address, "
        "customer_information, selling_company_id, creator_id, created_at, updated_at) values (:n, :org, 'DISPATCHED', :o, "
        "cast(:items as json), cast(:bill as json), cast(:ship as json), cast(:info as json), :c, :owner, now(), now()) returning id",
        n=f"GP Customer {TAG}", org=f"GP Trust {TAG}", o=number, c=company_id, owner=OWNER,
        items=json.dumps([{"product": '75" Interactive Flat Panel', "sku": f"GP-PANEL-{TAG}", "qty": len(serials)},
                          {"product": "Installation", "sku": "ADDON:Installation", "qty": 1}]),
        bill=json.dumps({"street": "Khadur Sahib", "city": "Taran Taran", "state": "Punjab", "pin": "143117"}),
        ship=json.dumps({"street": "Site 2", "city": "Amritsar", "state": "Punjab", "pin": "143001"}),
        info=json.dumps({"gst": "03AABTN1435H1Z1"}),
    )
    order_ids.append(oid)
    for serial in serials:
        movements_col.insert_one({"order_id": oid, "order_number": number, "direction": "OUT", "by_serial": True, "line": 0,
                                  "serial_number": serial, "company_id": company_id, "quantity": 1, "gp_test_tag": TAG})
    return oid


def out_for_delivery(oid):
    return api("put", f"/fulfilment/orders/{oid}/decide", admin,
               json={"approve": True, "vehicle_number": "up17 ct 1003", "driver_name": "Ravi Kumar"})


def passes(token, oid):
    r = api("get", "/gate-passes/", token, params={"sales_order_id": oid})
    return data(r) if r.status_code == 200 else []


try:
    a, b = company("A"), company("B")

    print("\nOut for delivery makes the gate pass")
    first = order("1", a, [f"GPS{TAG}-1", f"GPS{TAG}-2"])
    r = out_for_delivery(first)
    check("the order goes out for delivery", r.status_code == 200 and data(r)["status"] == "OUT_FOR_DELIVERY", r.text[:200])
    made = passes(admin, first)
    gp = made[0] if made else {}
    check(f"with gate pass {YEAR}/001", len(made) == 1 and gp.get("gate_pass_no") == f"{YEAR}/001", str(made)[:200])
    check("listing both panels by serial, and not the installation",
          sorted(x["serial"] for x in gp.get("lines", [])) == [f"GPS{TAG}-1", f"GPS{TAG}-2"] and gp.get("total_qty") == 2,
          str(gp.get("lines"))[:200])
    check("with the vehicle and driver, Bill To and Ship To from the order",
          gp.get("vehicle_number") == "UP17 CT 1003" and gp.get("driver_name") == "Ravi Kumar"
          and "Taran Taran" in gp.get("bill_to", {}).get("address", "") and "Amritsar" in gp.get("ship_to", {}).get("address", "")
          and gp.get("bill_to", {}).get("gstin") == "03AABTN1435H1Z1", str(gp)[:300])
    check("and the activity log names it", scalar(
        "select count(*) from sales_order_activity where sales_order_id=:o and description like :d",
        o=first, d=f"%Gate pass {YEAR}/001%") == 1)
    r = api("post", f"/gate-passes/orders/{first}", admin)
    check("asking again gives the same pass", r.status_code == 200 and data(r)["id"] == gp.get("id"), r.text[:160])

    second = order("2", a, [f"GPS{TAG}-3"])
    out_for_delivery(second)
    check(f"the next order of the company gets {YEAR}/002", [p["gate_pass_no"] for p in passes(admin, second)] == [f"{YEAR}/002"])
    other = order("3", b, [f"GPS{TAG}-4"])
    out_for_delivery(other)
    check("another company's numbers start at 001", [p["gate_pass_no"] for p in passes(admin, other)] == [f"{YEAR}/001"])

    print("\nAn order with no company")
    orphan = order("4", None, [])
    movements_col.delete_many({"order_id": orphan})
    r = out_for_delivery(orphan)
    check("still goes out for delivery", r.status_code == 200 and data(r)["status"] == "OUT_FOR_DELIVERY", r.text[:200])
    check("with no pass, and the reason on its activity", passes(admin, orphan) == [] and scalar(
        "select count(*) from sales_order_activity where sales_order_id=:o and description like '%No gate pass yet%'", o=orphan) == 1)
    r = api("post", f"/gate-passes/orders/{orphan}", admin)
    check("making one by hand says why it cannot", r.status_code == 400 and "selling company" in r.text, r.text[:160])

    print("\nA dispatch with no typed number")
    session = SessionLocal()
    from app.models.dispatch import DispatchOrder
    from app.models.sales_order import SalesOrder
    from app.services.stock.gate_pass import for_dispatch

    store = scalar("select id::text from locations where company_id=:c", c=a)
    do = DispatchOrder(do_number=f"DO-GP-{TAG}", company_id=uuid.UUID(a), warehouse_id=uuid.UUID(store), sales_order_id=second,
                       status="PACKED", challan_no="DC-9")
    session.add(do)
    session.flush()
    made_for_do = for_dispatch(do, session.get(SalesOrder, second), {"is_super_admin": True}, session)
    session.commit()
    check(f"gets {YEAR}/003, written onto the dispatch", made_for_do.gate_pass_no == f"{YEAR}/003" and do.gate_pass_no == f"{YEAR}/003",
          f"{made_for_do.gate_pass_no} {do.gate_pass_no}")
    session.close()

    print("\nTyped numbers, a lost company, a re-issued order")
    session = SessionLocal()
    twice = []
    for n in (1, 2):
        typed = DispatchOrder(do_number=f"DO-GPT{n}-{TAG}", company_id=uuid.UUID(a), warehouse_id=uuid.UUID(store),
                              sales_order_id=second, status="PACKED", challan_no="DC-T", gate_pass_no="GP-SAME")
        session.add(typed)
        session.flush()
        twice.append(for_dispatch(typed, session.get(SalesOrder, second), {"is_super_admin": True}, session).gate_pass_no)
    session.commit()
    session.close()
    check("the same typed number on two dispatches is kept on both, as before", twice == ["GP-SAME", "GP-SAME"], str(twice))

    lost = order("5", None, [f"GPS{TAG}-9"])
    movements_col.update_many({"order_id": lost}, {"$set": {"company_id": str(uuid.uuid4())}})
    r = out_for_delivery(lost)
    check("an order whose units name a company that is gone still goes out, without a pass",
          r.status_code == 200 and data(r)["status"] == "OUT_FOR_DELIVERY" and passes(admin, lost) == [], r.text[:200])

    again = order("6", a, [f"GPS{TAG}-7"])
    movements_col.insert_one({"order_id": again, "direction": "OUT", "by_serial": True, "line": 0,
                              "serial_number": f"GPS{TAG}-7", "company_id": a, "quantity": 1, "gp_test_tag": TAG})
    out_for_delivery(again)
    check("a unit issued twice (held and re-issued) is listed once",
          [x["serial"] for p in passes(admin, again) for x in p["lines"]] == [f"GPS{TAG}-7"])

    print("\nGenerated at Dispatched, before it goes out")
    early = order("7", b, [f"GPS{TAG}-8"])
    r = api("post", f"/gate-passes/orders/{early}", admin, json={})
    check("without the vehicle and driver it is refused", r.status_code == 400 and "vehicle number" in r.text, r.text[:160])
    r = api("post", f"/gate-passes/orders/{early}", admin,
            json={"vehicle_number": "X", "driver_name": "Y", "eway_bill_no": "1" * 41})
    check("an e-way bill number longer than 40 is refused", r.status_code == 422, str(r.status_code))
    r = api("post", f"/gate-passes/orders/{early}", admin,
            json={"vehicle_number": "ka 01 ab 1", "driver_name": "First Driver",
                  "eway_bill_no": " 3310 1234 5678 ", "transporter_id": "09aaacb1234c1z5"})
    early_pass = data(r) if r.status_code == 200 else {}
    check("with them, Generate Gate Pass makes the next number of the company",
          r.status_code == 200 and early_pass.get("gate_pass_no") == f"{YEAR}/002" and early_pass.get("vehicle_number") == "KA 01 AB 1",
          r.text[:200])
    check("with the e-way bill number and transporter ID typed",
          early_pass.get("eway_bill_no") == "3310 1234 5678" and early_pass.get("docket_no") == "09AAACB1234C1Z5", str(early_pass)[:300])
    r = api("post", f"/gate-passes/orders/{early}", admin,
            json={"vehicle_number": "ka 01 ab 1", "driver_name": "First Driver", "eway_bill_no": "999988887777",
                  "transporter_id": ""})
    updated = data(r) if r.status_code == 200 else {}
    check("typing them again updates the same pass: e-way bill changed, transporter ID cleared, number kept",
          updated.get("id") == early_pass.get("id") and updated.get("gate_pass_no") == early_pass.get("gate_pass_no")
          and updated.get("eway_bill_no") == "999988887777" and updated.get("docket_no") is None, str(updated)[:300])
    r = api("post", f"/gate-passes/orders/{early}", admin, json={"vehicle_number": "ka 01 ab 1", "driver_name": "First Driver"})
    check("leaving them out keeps what the pass has", data(r).get("eway_bill_no") == "999988887777", r.text[:200])
    check("the order is still Dispatched", scalar("select status from sales_order where id=:o", o=early) == "DISPATCHED")
    r = api("put", f"/fulfilment/orders/{early}/decide", admin,
            json={"approve": True, "vehicle_number": "ka 02 cd 2", "driver_name": "Second Driver"})
    same = passes(admin, early)
    check("marking it out for delivery uses the same pass, not a second number",
          r.status_code == 200 and len(same) == 1 and same[0]["id"] == early_pass.get("id"), str(same)[:200])
    check("with the vehicle and driver it actually left with",
          same and same[0]["vehicle_number"] == "KA 02 CD 2" and same[0]["driver_name"] == "Second Driver", str(same)[:200])
    print("\nAn order with no addresses of its own")
    bare = order("9", b, [f"GPS{TAG}-10"])
    scalar("update sales_order set billing_address=null, shipping_address=null where id=:o returning id", o=bare)
    scalar("insert into sales_proforma_invoice (sales_order_id, status, customer_name, billing_address, shipping_address, "
           "created_at, updated_at) values (:o, 'GENERATED', 'x', cast(:bill as json), cast(:ship as json), now(), now()) returning id",
           o=bare, bill=json.dumps({"street": "PI Bill Road", "city": "Ludhiana", "state": "Punjab", "pin": "141001"}),
           ship=json.dumps({"street": "PI Site", "city": "Jalandhar", "state": "Punjab", "pin": "144001"}))
    r = api("post", f"/gate-passes/orders/{bare}", admin, json={"vehicle_number": "PB 01 A 1", "driver_name": "Driver"})
    bare_pass = data(r) if r.status_code == 200 else {}
    check("takes Bill To and Ship To from its proforma invoice",
          "Ludhiana" in bare_pass.get("bill_to", {}).get("address", "")
          and "Jalandhar" in bare_pass.get("ship_to", {}).get("address", ""), str(bare_pass)[:300])

    ready = scalar("update sales_order set status='READY' where id=:o returning id", o=order("8", b, []))
    r = api("post", f"/gate-passes/orders/{ready}", admin, json={"vehicle_number": "X1", "driver_name": "Y"})
    check("an order not yet dispatched is refused", r.status_code == 409 and "not dispatched" in r.text, r.text[:160])

    print("\nWho may make and read it")
    r = api("post", f"/gate-passes/orders/{first}", salesperson)
    check("someone not in logistics cannot make one", r.status_code == 403, str(r.status_code))
    check("someone not on the order's line or a desk cannot read it",
          api("get", f"/gate-passes/{gp.get('id')}", salesperson).status_code == 404 and passes(salesperson, first) == [])
    r = api("get", f"/gate-passes/{gp.get('id')}/pdf", admin)
    check("the PDF downloads", r.status_code == 200 and r.content[:4] == b"%PDF"
          and "Gate_Pass_" in r.headers.get("content-disposition", ""), str(r.status_code))
finally:
    movements_col.delete_many({"gp_test_tag": TAG})
    session = SessionLocal()
    for oid in order_ids:
        session.execute(text("delete from gate_pass where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from dispatch_order where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order_line where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order_activity where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_proforma_invoice where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order where id=:o"), {"o": oid})
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
