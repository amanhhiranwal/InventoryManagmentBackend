"""The Inventory list and the store ledger kept in step while the ledger is off.

  * Production's serial check names the store holding the serial, and
    refuses one the ledger has but not on the shelf;
  * issuing a Bangalore serial at Production takes it out of the Bangalore
    store (not Noida's), and putting the order on hold puts it back;
  * goods accepted on a GRN appear on the Inventory list - new serials as
    their own records, counted goods added to the count;
  * the ledger agrees with its balances throughout.

    docker exec -w /app backend_app python test_catalogue_sync.py
"""

import json
import sys
import uuid

from sqlalchemy import text

from app.database.postgres import SessionLocal
from app.services.inventory_service import InventoryService
from app.services.stock_movement_service import movements_col
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, api, login

TAG = uuid.uuid4().hex[:6].upper()
passed, failed = [], []


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


def sql(statement, **params):
    session = SessionLocal()
    try:
        session.execute(text(statement), params)
        session.commit()
    finally:
        session.close()


admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
company_ids, order_ids = [], []
PANEL, CABLE = f"CSY-PANEL-{TAG}", f"CSY-CABLE-{TAG}"
BLR = [f"CSB{TAG}-1", f"CSB{TAG}-2"]
NOI = f"CSN{TAG}-1"


def serial_status(serial):
    return scalar("select status || '@' || warehouse_id::text from stock_serial where serial_no = :s", s=serial)


def add_product(company_id, store_id, serials, model=PANEL, count=None):
    r = api("post", "/inventory/items", admin, json={
        "name": f"CSY {model}", "serial_number": "/".join(serials), "product_type_code": "IFP", "category": "IFP",
        "attributes": {"rate": 100, "unit": "Pcs", "instock": count if count is not None else len(serials), "case_size": 1,
                       "model_number": model, "specification": "t"},
        "company_id": company_id,
    })
    assert r.status_code == 200, r.text
    InventoryService.items_col.update_many({"attributes.model_number": model}, {"$set": {"csy_test_tag": TAG}})
    r = api("post", "/stock/product-stock", admin, json={
        "company_id": company_id, "warehouse_id": store_id, "sku": model,
        "quantity": count if count is not None else len(serials), "serials": serials, "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text


try:
    r = api("post", "/companies/", admin, json={
        "company_name": f"CSY Test {TAG}", "company_code": f"CSY{TAG}", "email": f"csy.{TAG.lower()}@mailinator.com",
        "phone_number": "9800000071", "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh",
        "country": "India", "postal_code": "201301"})
    a = data(r)["id"]
    company_ids.append(a)
    noida = scalar("select id::text from locations where company_id = :c", c=a)
    r = api("post", "/locations/", admin, json={"company_id": a, "location_name": f"Bangalore {TAG}", "location_code": f"CSB{TAG}",
                                               "email": f"csy.blr.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    blr = data(r)["id"]
    add_product(a, blr, BLR)
    add_product(a, noida, [NOI])

    print("\nProduction's serial check")
    r = api("get", "/fulfilment/serials/check", admin, params={"serial": BLR[0]})
    check("a Bangalore serial is reported in the Bangalore store", data(r).get("store") == f"Bangalore {TAG}"
          and data(r).get("in_stock") is True, r.text[:200])
    sql("update stock_serial set status = 'QUARANTINE' where serial_no = :s", s=BLR[1])
    r = api("get", "/fulfilment/serials/check", admin, params={"serial": BLR[1]})
    check("one the ledger has in quality check is refused, naming the store",
          data(r).get("in_stock") is False and "in quality check" in (data(r).get("message") or "")
          and f"Bangalore {TAG}" in data(r)["message"], r.text[:200])
    sql("update stock_serial set status = 'IN_STOCK' where serial_no = :s", s=BLR[1])

    print("\nIssuing at Production")
    oid = scalar(
        "insert into sales_order (customer_name, status, order_number, items, selling_company_id, created_at, updated_at) "
        "values (:n, 'PROCUREMENT', :o, cast(:i as json), :c, now(), now()) returning id",
        n=f"CSY Customer {TAG}", o=f"SO-CSY-{TAG}", c=a,
        i=json.dumps([{"product": f"CSY {PANEL}", "sku": PANEL, "model": PANEL, "qty": 1, "quantity_case": 1}]))
    order_ids.append(oid)
    r = api("put", f"/fulfilment/orders/{oid}/decide", admin, json={"approve": True, "serials": [{"line": 0, "serial": BLR[0]}]})
    check("production issue the Bangalore serial", r.status_code == 200 and data(r)["status"] == "READY", r.text[:200])
    check("it leaves the Bangalore store in the ledger", scalar("select status from stock_serial where serial_no=:s", s=BLR[0]) == "DISPATCHED"
          and scalar("select warehouse_id::text from inventory_ledger where source_doc_type='ORDER_ISSUE' and serial_no=:s", s=BLR[0]) == blr)
    check("Bangalore holds 1 panel, Noida still 1", scalar(
        "select qty_on_hand from stock_balance where warehouse_id=:w and sku=:s", w=blr, s=PANEL) == 1 and scalar(
        "select qty_on_hand from stock_balance where warehouse_id=:w and sku=:s", w=noida, s=PANEL) == 1)
    check("and it is gone from the Inventory list", InventoryService.items_col.count_documents(
        {"serial_number": BLR[0], "issued": True}) == 1)

    print("\nOn hold puts it back")
    r = api("put", f"/fulfilment/orders/{oid}/decide", admin, json={"approve": False, "remarks": "Customer asked to wait"})
    check("the order goes on hold", r.status_code == 200 and data(r)["status"] == "ON_HOLD", r.text[:200])
    check("the serial is back on the shelf in Bangalore", serial_status(BLR[0]) == f"IN_STOCK@{blr}", str(serial_status(BLR[0])))
    check("Bangalore holds 2 again", scalar(
        "select qty_on_hand from stock_balance where warehouse_id=:w and sku=:s", w=blr, s=PANEL) == 2)

    print("\nCounted goods issued by a lot code")
    LOT_MODEL, LOT = f"CSY-MOUNT-{TAG}", f"CSL{TAG}-LOT"
    r = api("post", "/inventory/items", admin, json={
        "name": f"CSY Mount {TAG}", "serial_number": LOT, "product_type_code": "IFP", "category": "IFP",
        "attributes": {"rate": 10, "unit": "Pcs", "instock": 5, "case_size": 1, "model_number": LOT_MODEL, "specification": "t"},
        "company_id": a})
    InventoryService.items_col.update_many({"attributes.model_number": LOT_MODEL}, {"$set": {"csy_test_tag": TAG}})
    api("post", "/stock/product-stock", admin, json={"company_id": a, "warehouse_id": noida, "sku": LOT_MODEL, "quantity": 5,
                                                     "serials": [], "request_id": str(uuid.uuid4())})
    mount = lambda: scalar("select qty_on_hand from stock_balance where warehouse_id=:w and sku=:s", w=noida, s=LOT_MODEL)
    oid2 = scalar(
        "insert into sales_order (customer_name, status, order_number, items, selling_company_id, created_at, updated_at) "
        "values (:n, 'PROCUREMENT', :o, cast(:i as json), :c, now(), now()) returning id",
        n=f"CSY Customer {TAG}", o=f"SO-CSY2-{TAG}", c=a,
        i=json.dumps([{"product": f"CSY Mount {TAG}", "sku": LOT_MODEL, "model": LOT_MODEL, "qty": 1, "quantity_case": 1}]))
    order_ids.append(oid2)
    r = api("put", f"/fulfilment/orders/{oid2}/decide", admin, json={"approve": True, "serials": [{"line": 0, "serial": LOT}]})
    check("production issue one mount by its lot code", r.status_code == 200 and data(r)["status"] == "READY", r.text[:200])
    check("one mount leaves the Noida store (5 -> 4)", mount() == 4, str(mount()))
    api("put", f"/fulfilment/orders/{oid2}/decide", admin, json={"approve": False, "remarks": "Wait"})
    check("on hold, it goes back (4 -> 5)", mount() == 5, str(mount()))

    print("\nGRN-accepted goods on the Inventory list")
    add_product(a, noida, [], model=CABLE, count=5)
    r = api("post", "/grn/", admin, json={"warehouse_id": blr, "receipt_type": "OTHER", "supplier_name": f"CSY Supplier {TAG}",
                                          "lines": [{"sku": PANEL, "qty_received": 2}, {"sku": CABLE, "qty_received": 3}]})
    grn = data(r)
    api("post", f"/grn/{grn['id']}/submit", admin)
    api("post", f"/grn/{grn['id']}/receive", admin)
    new_serials = [f"CSG{TAG}-1", f"CSG{TAG}-2"]
    lines = {line["sku"]: line["id"] for line in grn["lines"]}
    r = api("post", f"/grn/{grn['id']}/inspect", admin, json={"lines": [
        {"line_id": lines[PANEL], "qty_accepted": 2, "qty_rejected": 0, "serials": new_serials},
        {"line_id": lines[CABLE], "qty_accepted": 3, "qty_rejected": 0}]})
    check("the GRN posts", r.status_code == 200 and data(r)["status"] == "POSTED", r.text[:200])
    check("both new serials are on the Inventory list, 1 each, for the company",
          InventoryService.items_col.count_documents({"serial_number": {"$in": new_serials}, "company_id": a,
                                                      "attributes.instock": 1}) == 2)
    cable = InventoryService.items_col.find_one({"attributes.model_number": CABLE, "serial_number": ""})
    check("the counted cables went from 5 to 8", cable and float(cable["attributes"]["instock"]) == 8, str(cable and cable["attributes"]))
    r = api("get", "/fulfilment/serials/check", admin, params={"serial": new_serials[0]})
    check("and Production can issue a GRN unit, seen in Bangalore", data(r).get("in_stock") is True
          and data(r).get("store") == f"Bangalore {TAG}", r.text[:200])

    mismatches = [m for m in data(api("get", "/stock/reconcile", admin)) if m["company_id"] in company_ids]
    check("balances agree with the ledger", mismatches == [], str(mismatches))
finally:
    InventoryService.items_col.delete_many({"$or": [{"csy_test_tag": TAG}, {"attributes.model_number": {"$in": [PANEL, CABLE, f"CSY-MOUNT-{TAG}"]}}]})
    movements_col.delete_many({"order_id": {"$in": order_ids}})
    session = SessionLocal()
    for oid in order_ids:
        session.execute(text("delete from notifications where module='sales_order' and entity_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order_activity where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order_line where sales_order_id=:o"), {"o": oid})
        session.execute(text("delete from sales_order where id=:o"), {"o": oid})
    for cid in company_ids:
        session.execute(text("delete from goods_receipt_line where grn_id in (select id from goods_receipt where company_id=:c)"), {"c": cid})
        session.execute(text("delete from goods_receipt where company_id=:c"), {"c": cid})
        session.execute(text("delete from document_counter where name like :n"), {"n": f"grn:{cid}:%"})
        for table in ("stock_serial", "inventory_ledger", "stock_balance"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": cid})
        session.execute(text("delete from warehouse_bins where warehouse_id in (select id from locations where company_id = :c)"), {"c": cid})
        session.execute(text("delete from locations where company_id = :c"), {"c": cid})
        session.execute(text("delete from user_companies where company_id = :c"), {"c": cid})
        session.execute(text("delete from companies where id = :c"), {"c": cid})
    session.commit()
    session.close()
    print("\nCleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
