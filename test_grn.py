"""Stock In (GRN) end to end through the API.

A PO for Company A of 10 panels and 5 cables, received at A's warehouse.
Checks the rules the business relies on:

  * a PO, a draft GRN and a submitted GRN move no stock;
  * a GRN cannot receive more than is still to come on a PO line, and two
    drafts cannot both claim the same open units;
  * a PO of one company cannot be received into another company's warehouse;
  * posting the receipt puts everything into QUARANTINE and moves the PO;
    QC then releases accepted goods (with serials) to available stock,
    rejected to REJECTED and damaged to BLOCKED - none of them saleable;
  * a rejection needs a reason; accepted + rejected must equal received;
  * posting twice, or four people at once, moves stock once;
  * partial receipts accumulate and close the PO as fully received;
  * a return without a PO posts as a customer return, into quarantine;
  * without the permissions, or outside the warehouse, it is refused;
  * GRN numbers run per company per financial year: 2026-27/001.

    docker exec -w /app backend_app python test_grn.py
"""

import sys
import threading
import uuid
from datetime import datetime

from sqlalchemy import text

from app.database.postgres import SessionLocal
from app.models.procurement import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequisition,
    Vendor,
    VendorQuotation,
)
from app.services.inventory_service import InventoryService
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
company_ids, location_ids, user_ids, po_ids, procurement = [], [], [], [], {}
PANEL, CABLE = f"GRN-PANEL-{TAG}", f"GRN-CABLE-{TAG}"


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"GRN Test {letter} {TAG}", "company_code": f"GR{letter}{TAG}",
        "email": f"grn.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000041",
        "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    assert r.status_code < 400, r.text
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def warehouse(company_id, code):
    r = api("post", "/locations/", admin, json={
        "company_id": company_id, "location_name": f"Noida {code}", "location_code": f"{code}{TAG}",
        "email": f"grn.wh.{code.lower()}.{TAG.lower()}@mailinator.com", "city": "Noida", "location_type": "WAREHOUSE",
    })
    assert r.status_code < 400, r.text
    location_ids.append(data(r)["id"])
    return data(r)


def person(label, permissions, company_list, warehouses):
    email = f"grn.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "Grn", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-GRN-{label}-{TAG}", "phone_number": "9800000042", "role_ids": [], "company_ids": company_list,
    })
    assert r.status_code < 400, r.text
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'grn test', now(), now())",
        i=rid, n=f"GRN Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    r = api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list, "warehouse_ids": warehouses})
    assert r.status_code < 400, r.text
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def make_po(company_id, lines, status="APPROVED"):
    session = SessionLocal()
    try:
        if "vendor" not in procurement:
            vendor = Vendor(vendor_code=f"V{TAG}", name=f"GRN Vendor {TAG}", address="Nehru Place, New Delhi")
            session.add(vendor)
            session.flush()
            procurement["vendor"] = vendor.id
        n = len(po_ids) + 1
        pr = PurchaseRequisition(pr_number=f"PR-GRN-{TAG}-{n}", requested_by=uuid.uuid4(), purchase_type="Stock", company_id=company_id)
        session.add(pr)
        session.flush()
        quote = VendorQuotation(quotation_number=f"VQ-GRN-{TAG}-{n}", requisition_id=pr.id, vendor_id=procurement["vendor"])
        session.add(quote)
        session.flush()
        po = PurchaseOrder(po_number=f"PO-GRN-{TAG}-{n}", requisition_id=pr.id, vendor_id=procurement["vendor"], quotation_id=quote.id,
                           vendor_name=f"GRN Vendor {TAG}", vendor_address="Nehru Place, New Delhi", status=status, po_date=datetime.utcnow())
        session.add(po)
        session.flush()
        items = []
        for sku, quantity, rate in lines:
            item = PurchaseOrderItem(purchase_order_id=po.id, product_name=f"Item {sku}", sku=sku, quantity=quantity, unit_rate=rate, uom="Nos")
            session.add(item)
            session.flush()
            items.append(item.id)
        session.commit()
        po_ids.append(po.id)
        procurement.setdefault("prs", []).append(pr.id)
        procurement.setdefault("quotes", []).append(quote.id)
        return po.id, items
    finally:
        session.close()


def product(sku, company_id):
    InventoryService.items_col.insert_one({
        "name": f"GRN Test {sku}", "serial_number": "", "product_type_code": "TEST", "category": "Test", "company_id": company_id,
        "attributes": {"model_number": sku, "specification": "test", "unit": "Nos", "instock": 0}, "grn_test_tag": TAG,
    })


def on_hand(token, warehouse_id, sku, status="UNRESTRICTED"):
    rows = data(api("get", "/stock/balances", token, params={"warehouse_id": warehouse_id, "search": sku, "size": 50}))
    return sum(r["qty_on_hand"] for r in rows if r["sku"] == sku and r["stock_status"] == status)


def ledger_rows(grn_id):
    return scalar("select count(*) from inventory_ledger where source_doc_type = 'GRN' and source_doc_id = :d", d=str(grn_id))


def po_state(po_id):
    session = SessionLocal()
    try:
        po = session.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).one()
        return po.status, {i.sku: i.received_quantity for i in po.items}
    finally:
        session.close()


try:
    a, b = company("A"), company("B")
    wa, wb = warehouse(a, "GA"), warehouse(b, "GB")
    product(PANEL, a)
    product(CABLE, a)
    product(PANEL, b)
    store = lambda wh: {x["code"]: x for x in data(api("get", f"/locations/{wh}/bins", admin))}  # noqa: E731
    bins_a = store(wa["id"])

    keeper = person("keeper", ["grn.read", "grn.create", "stock.read"], [a], [wa["id"]])
    inspector = person("inspector", ["grn.read", "grn.post", "grn.approve", "stock.read"], [a], [wa["id"]])
    keeper_b = person("keeperb", ["grn.read", "grn.create"], [b], [wb["id"]])

    po_a, (item_panel, item_cable) = make_po(a, [(PANEL, 10, 50000), (CABLE, 5, 300)])
    po_b, _ = make_po(b, [(PANEL, 4, 50000)])

    print("\nA purchase order moves no stock")
    check("nothing on hand after the PO", on_hand(keeper, wa["id"], PANEL) == 0)
    receivable = data(api("get", "/grn/purchase-orders", keeper, params={"warehouse_id": wa["id"]}))
    check("A's warehouse lists only A's open POs", [p["id"] for p in receivable] == [po_a], str([p["id"] for p in receivable]))

    print("\nDrafting")

    def grn(token, warehouse_id, lines, **extra):
        body = {"warehouse_id": warehouse_id, "receipt_type": "PURCHASE", "purchase_order_id": po_a,
                "delivery_note_no": "3639", "delivery_note_date": "2026-11-14", "transport_remarks": "Received from PORTER",
                "lines": lines, **extra}
        return api("post", "/grn/", token, json=body)

    r = grn(keeper, wa["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 11}])
    check("receiving more than ordered is refused", r.status_code == 400 and "still to come" in r.text, r.text[:140])
    r = grn(keeper_b, wb["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 1}])
    check("A's PO cannot be received into B's warehouse", r.status_code == 400 and "another company" in r.text, r.text[:140])
    r = grn(keeper, wb["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 1}])
    check("a warehouse the storekeeper does not work in is not found", r.status_code == 404, str(r.status_code))
    r = grn(inspector, wa["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 1}])
    check("the inspector alone cannot record a GRN", r.status_code == 403, str(r.status_code))
    r = api("get", "/grn/", salesperson)
    check("no GRN permission, no GRNs", r.status_code == 403, str(r.status_code))

    r = grn(keeper, wa["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 6},
                               {"po_item_id": item_cable, "sku": CABLE, "qty_received": 5}])
    first = data(r)
    check("a GRN draft is saved in the sheet's numbering", r.status_code == 200 and first["grn_number"].endswith("/001")
          and first["grn_number"].count("-") == 1, r.text[:160])
    check("supplier and PO come from the PO", first["supplier_name"] == f"GRN Vendor {TAG}" and first["po_number"] == f"PO-GRN-{TAG}-1")
    check("unit cost comes from the PO rate", first["lines"][0]["unit_cost"] == 50000.0)
    check("a draft moves no stock", on_hand(keeper, wa["id"], PANEL) == 0 and ledger_rows(first["id"]) == 0)
    r = grn(keeper, wa["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 5}])
    check("a second draft cannot claim units the first already holds", r.status_code == 400 and "4" in r.text, r.text[:140])

    r = api("post", f"/grn/{first['id']}/receive", inspector)
    check("a draft's receipt cannot be posted before it is sent in", r.status_code == 409, str(r.status_code))
    r = api("post", f"/grn/{first['id']}/submit", keeper)
    check("the storekeeper sends it in", r.status_code == 200 and data(r)["status"] == "SUBMITTED")
    check("a submitted GRN moves no stock", on_hand(keeper, wa["id"], PANEL, "QUARANTINE") == 0 and ledger_rows(first["id"]) == 0)
    r = api("put", f"/grn/{first['id']}", keeper, json={"warehouse_id": wa["id"], "purchase_order_id": po_a,
                                                          "lines": [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 1}]})
    check("a submitted GRN cannot be edited", r.status_code == 409, str(r.status_code))
    r = api("post", f"/grn/{first['id']}/inspect", inspector, json={"lines": [{"line_id": first["lines"][0]["id"], "qty_accepted": 6, "qty_rejected": 0}]})
    check("QC cannot start before the receipt is posted", r.status_code == 409 and "quarantine" in r.text, r.text[:120])

    print("\nPosting the receipt into quarantine")
    r = api("post", f"/grn/{first['id']}/receive", keeper)
    check("the storekeeper alone cannot post the receipt", r.status_code == 403, str(r.status_code))
    results = []

    def receive():
        results.append(api("post", f"/grn/{first['id']}/receive", inspector).status_code)

    threads = [threading.Thread(target=receive) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("four people posting the receipt at once: exactly one succeeds", sorted(results) == [200, 409, 409, 409], str(results))
    check("everything received is in quarantine", on_hand(keeper, wa["id"], PANEL, "QUARANTINE") == 6.0
          and on_hand(keeper, wa["id"], CABLE, "QUARANTINE") == 5.0)
    check("and none of it is available yet", on_hand(keeper, wa["id"], PANEL) == 0 and on_hand(keeper, wa["id"], CABLE) == 0)
    check("it sits in the QC bin", scalar("select sum(qty_on_hand) from stock_balance where company_id = :c and bin_id = :b",
                                          c=a, b=bins_a["QC"]["id"]) == 11)
    check("one receipt row per line in the ledger", ledger_rows(first["id"]) == 2, str(ledger_rows(first["id"])))
    status, received = po_state(po_a)
    check("the PO shows 6 panels and 5 cables received, partially received", status == "PARTIALLY_RECEIVED"
          and received == {PANEL: 6.0, CABLE: 5.0}, f"{status} {received}")
    check("the receipt is a purchase receipt", scalar(
        "select string_agg(distinct movement_type, ',') from inventory_ledger where source_doc_type='GRN' and source_doc_id=:d",
        d=str(first["id"])) == "PURCHASE_RECEIPT")
    r = api("post", f"/grn/{first['id']}/cancel", keeper, json={"reason": "Too late"})
    check("once the receipt is posted the GRN cannot be cancelled", r.status_code == 409, str(r.status_code))

    print("\nQuality check")
    panel_line, cable_line = first["lines"][0]["id"], first["lines"][1]["id"]
    serials = [f"GS{TAG}-{i}" for i in range(1, 6)]

    def inspect(token, grn_id, decisions):
        return api("post", f"/grn/{grn_id}/inspect", token, json={"lines": decisions})

    r = inspect(inspector, first["id"], [{"line_id": panel_line, "qty_accepted": 5, "qty_rejected": 0},
                                         {"line_id": cable_line, "qty_accepted": 5, "qty_rejected": 0}])
    check("accepted + rejected + damaged must equal received", r.status_code == 400 and "must equal" in r.text, r.text[:140])
    r = inspect(inspector, first["id"], [{"line_id": panel_line, "qty_accepted": 5, "qty_rejected": 1},
                                         {"line_id": cable_line, "qty_accepted": 5, "qty_rejected": 0}])
    check("a rejection needs a reason", r.status_code == 400 and "reason" in r.text, r.text[:140])
    r = inspect(inspector, first["id"], [{"line_id": panel_line, "qty_accepted": 5, "qty_rejected": 1}])
    check("every line must be inspected", r.status_code == 400 and "line(s) 2" in r.text, r.text[:140])
    r = inspect(keeper, first["id"], [{"line_id": panel_line, "qty_accepted": 6, "qty_rejected": 0},
                                      {"line_id": cable_line, "qty_accepted": 5, "qty_rejected": 0}])
    check("the storekeeper alone cannot post QC", r.status_code == 403, str(r.status_code))
    r = inspect(inspector, first["id"], [{"line_id": panel_line, "qty_accepted": 5, "qty_rejected": 1, "remarks": "x",
                                          "bin_id": bins_a["REJECT"]["id"]},
                                         {"line_id": cable_line, "qty_accepted": 5, "qty_rejected": 0}])
    check("accepted goods cannot be put into the rejected bin", r.status_code == 400 and "store bin" in r.text, r.text[:140])

    decisions = [{"line_id": panel_line, "qty_accepted": 4, "qty_rejected": 1, "qty_damaged": 1, "remarks": "Screen cracked",
                  "serials": serials[:4]},
                 {"line_id": cable_line, "qty_accepted": 5, "qty_rejected": 0}]
    results = []

    def post_it():
        results.append(inspect(inspector, first["id"], decisions).status_code)

    threads = [threading.Thread(target=post_it) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("four inspectors posting QC at once: exactly one succeeds", sorted(results) == [200, 409, 409, 409], str(results))
    posted = data(api("get", f"/grn/{first['id']}", inspector))
    check("the GRN is generated with who inspected it", posted["status"] == "POSTED" and posted["inspected_by_name"].startswith("Grn inspector"),
          str(posted["inspected_by_name"]))
    check("nothing is left in quarantine", on_hand(keeper, wa["id"], PANEL, "QUARANTINE") == 0 and on_hand(keeper, wa["id"], CABLE, "QUARANTINE") == 0)
    check("only the 4 accepted panels are available", on_hand(keeper, wa["id"], PANEL) == 4.0, str(on_hand(keeper, wa["id"], PANEL)))
    check("1 rejected panel is in REJECTED", on_hand(keeper, wa["id"], PANEL, "REJECTED") == 1.0)
    check("1 damaged panel is BLOCKED", on_hand(keeper, wa["id"], PANEL, "BLOCKED") == 1.0)
    check("5 cables are available", on_hand(keeper, wa["id"], CABLE) == 5.0)
    qc_rows = scalar("select count(*) from inventory_ledger where source_doc_type = 'GRN_QC' and source_doc_id = :d", d=str(first["id"]))
    check("QC wrote: out of quarantine, a row per accepted serial, rejected, damaged, and the cables", qc_rows == 9, str(qc_rows))
    check("the accepted serials are on the shelf in STORE", scalar(
        "select count(*) from stock_serial where company_id = :c and serial_no = any(:s) and status = 'IN_STOCK' and bin_id = :b",
        c=a, s=serials[:4], b=bins_a["STORE"]["id"]) == 4)
    check("the summary shows rejected and blocked apart from available",
          data(api("get", "/stock/summary", keeper, params={"warehouse_id": wa["id"]}))["available"] == 9.0)

    print("\nThe rest of the PO")
    r = grn(keeper, wa["id"], [{"po_item_id": item_cable, "sku": CABLE, "qty_received": 1}])
    check("the cables are complete; nothing more can be received for them", r.status_code == 400, r.text[:120])
    r = grn(keeper, wa["id"], [{"po_item_id": item_panel, "sku": PANEL, "qty_received": 4}])
    second = data(r)
    check("the second GRN is numbered /002", r.status_code == 200 and second["grn_number"].endswith("/002"), r.text[:120])
    api("post", f"/grn/{second['id']}/submit", keeper)
    r = api("post", f"/grn/{second['id']}/receive", inspector)
    check("posting its receipt closes the PO as fully received", r.status_code == 200 and po_state(po_a)[0] == "FULLY_RECEIVED", r.text[:140])
    r = inspect(inspector, second["id"], [{"line_id": second["lines"][0]["id"], "qty_accepted": 4, "qty_rejected": 0}])
    check("8 panels available now", r.status_code == 200 and on_hand(keeper, wa["id"], PANEL) == 8.0, r.text[:120])
    check("a fully received PO is no longer offered", data(api("get", "/grn/purchase-orders", keeper, params={"warehouse_id": wa["id"]})) == [])

    print("\nA return with no PO")
    r = api("post", "/grn/", keeper, json={"warehouse_id": wa["id"], "receipt_type": "RETURN", "supplier_name": "Tech Transformation",
                                          "supplier_address": "Lucknow", "lines": [{"sku": PANEL, "qty_received": 2, "qty_ordered": 2}]})
    ret = data(r)
    check("a return is saved without a PO", r.status_code == 200 and ret["po_number"] is None and ret["supplier_name"] == "Tech Transformation",
          r.text[:140])
    api("post", f"/grn/{ret['id']}/submit", keeper)
    api("post", f"/grn/{ret['id']}/receive", inspector)
    check("a return goes into quarantine as a customer return, not a purchase", scalar(
        "select string_agg(distinct movement_type, ',') from inventory_ledger where source_doc_type='GRN' and source_doc_id=:d",
        d=str(ret["id"])) == "CUSTOMER_RETURN" and on_hand(keeper, wa["id"], PANEL, "QUARANTINE") == 2.0)
    inspect(inspector, ret["id"], [{"line_id": ret["lines"][0]["id"], "qty_accepted": 2, "qty_rejected": 0}])
    check("only after QC does it become available", on_hand(keeper, wa["id"], PANEL) == 10.0)
    r = api("post", "/grn/", keeper, json={"warehouse_id": wa["id"], "receipt_type": "OTHER", "lines": [{"sku": PANEL, "qty_received": 1}]})
    check("goods with no PO need a supplier named", r.status_code == 400 and "who the goods came from" in r.text, r.text[:120])
    r = api("post", "/grn/", keeper, json={"warehouse_id": wa["id"], "receipt_type": "OTHER", "supplier_name": "Sample",
                                          "lines": [{"sku": CABLE, "qty_received": "2.5"}]})
    check("a decimal quantity is kept as entered", r.status_code == 200 and data(r)["lines"][0]["qty_received"] == 2.5, r.text[:120])
    sample = data(r)

    print("\nCancelling and seeing")
    r = api("post", "/grn/", keeper, json={"warehouse_id": wa["id"], "receipt_type": "OTHER", "supplier_name": "Walk-in",
                                          "lines": [{"sku": CABLE, "qty_received": 3}]})
    third = data(r)
    r = api("post", f"/grn/{third['id']}/cancel", keeper, json={"reason": "Entered twice"})
    check("a draft can be cancelled with a reason", r.status_code == 200 and data(r)["status"] == "CANCELLED")
    api("post", f"/grn/{sample['id']}/submit", keeper)
    r = api("post", f"/grn/{sample['id']}/cancel", keeper, json={"reason": "Wrong delivery"})
    check("a submitted GRN can still be cancelled; no stock had moved", r.status_code == 200 and ledger_rows(sample["id"]) == 0)
    r = api("get", f"/grn/{first['id']}", keeper_b)
    check("B's storekeeper cannot open A's GRN", r.status_code == 404, str(r.status_code))
    listed = api("get", "/grn/", keeper).json()
    check("the list counts by status", listed["counts"].get("POSTED") == 3 and listed["counts"].get("CANCELLED") == 2, str(listed["counts"]))
    check("the letterhead is the GRN's company", api("get", f"/grn/{first['id']}", keeper).json()["letterhead"]["company_name"] == f"GRN Test A {TAG}")
    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger after all of it", mismatches == [], str(mismatches))
finally:
    InventoryService.items_col.delete_many({"grn_test_tag": TAG})
    session = SessionLocal()
    for cid in company_ids:
        session.execute(text("delete from stock_serial where company_id = :c"), {"c": cid})
        session.execute(text("delete from inventory_ledger where company_id = :c"), {"c": cid})
        session.execute(text("delete from stock_balance where company_id = :c"), {"c": cid})
        session.execute(text("delete from goods_receipt where company_id = :c"), {"c": cid})
    for pid in po_ids:
        session.execute(text("delete from purchase_order where id = :p"), {"p": pid})
    for qid in procurement.get("quotes", []):
        session.execute(text("delete from vendor_quotation where id = :q"), {"q": qid})
    for prid in procurement.get("prs", []):
        session.execute(text("delete from purchase_requisition where id = :p"), {"p": prid})
    if procurement.get("vendor"):
        session.execute(text("delete from vendor where id = :v"), {"v": procurement["vendor"]})
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
        session.execute(text("delete from document_counter where name like :n"), {"n": f"grn:{cid}:%"})
        session.execute(text("delete from user_companies where company_id = :c"), {"c": cid})
        session.execute(text("delete from companies where id = :c"), {"c": cid})
    session.commit()
    session.close()
    print("\nCleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
