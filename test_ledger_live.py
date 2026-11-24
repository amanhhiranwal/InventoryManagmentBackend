"""The order desks with the stock ledger switched on (stock_ledger_live).

While the switch is on:
  * production cannot mark an order ready until its goods are reserved,
    and marking it ready moves no stock and asks for no serials;
  * logistics cannot mark it dispatched until its dispatch orders have
    been committed for everything on it;
  * the Production desk's stock column reads the ledger;
  * cancelling the order gives back its reservations and cancels dispatch
    orders that have not left - but what was dispatched stays out;
  * the old MongoDB counting writes nothing.

The switch is restored to what it was afterwards.

    docker exec -w /app backend_app python test_ledger_live.py
"""

import json
import sys
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.core.stock import LEDGER_LIVE_SETTING, OPENING_BALANCE, UNRESTRICTED
from app.database.mongodb import sync_mongo_db
from app.database.postgres import SessionLocal
from app.services.stock import posting
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, api, login

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
SKU = f"LIVE-PANEL-{TAG}"
company_id = location_id = order_id = None
previous = scalar("select value from app_setting where key = :k", k=LEDGER_LIVE_SETTING)
mongo_before = sync_mongo_db["inventory_movements"].count_documents({})


def decide(approve=True, remarks=None):
    return api("put", f"/fulfilment/orders/{order_id}/decide", admin, json={"approve": approve, "remarks": remarks})


def status():
    return scalar("select status from sales_order where id = :o", o=order_id)


try:
    sql("insert into app_setting (key, value) values (:k, 'true') on conflict (key) do update set value = 'true'", k=LEDGER_LIVE_SETTING)

    r = api("post", "/companies/", admin, json={
        "company_name": f"Live Test {TAG}", "company_code": f"LV{TAG}", "email": f"live.{TAG.lower()}@mailinator.com",
        "phone_number": "9800000061", "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh",
        "country": "India", "postal_code": "201301",
    })
    company_id = data(r)["id"]
    r = api("post", "/locations/", admin, json={"company_id": company_id, "location_name": "Live WH", "location_code": f"LVW{TAG}",
                                                "email": f"live.wh.{TAG.lower()}@mailinator.com", "location_type": "WAREHOUSE"})
    location_id = data(r)["id"]
    store = next(b for b in data(api("get", f"/locations/{location_id}/bins", admin)) if b["code"] == "STORE")
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"LIVE-{TAG}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(company_id), warehouse_id=uuid.UUID(location_id), bin_id=uuid.UUID(store["id"]),
                                    sku=SKU, stock_status=UNRESTRICTED, quantity=Decimal(5), movement_type=OPENING_BALANCE, source_line_id="1")],
    ))
    order_id = session.execute(text(
        "insert into sales_order (customer_name, status, order_number, items, created_at, updated_at) "
        "values ('Live Customer', 'PROCUREMENT', :o, cast(:i as json), now(), now()) returning id"),
        {"o": f"SO-LIVE-{TAG}", "i": json.dumps([{"product": "Panel", "sku": SKU, "qty": 3, "quantity_case": 3}])}).scalar()
    session.commit()
    session.close()

    print("\nProduction: ready only when reserved")
    r = decide()
    check("marking ready before reserving is refused, pointing at Dispatch", r.status_code == 400 and "Reserve stock" in r.text, r.text[:160])
    check("and it stays in production", status() == "PROCUREMENT")
    lines = data(api("get", f"/dispatch/orders/{order_id}/availability", admin, params={"warehouse_id": location_id}))["lines"]
    api("post", f"/dispatch/orders/{order_id}/reserve", admin, json={"warehouse_id": location_id, "lines": [{"so_line_id": lines[0]["so_line_id"], "qty": 3}]})
    r = decide()
    check("once reserved, it is marked ready with no serials asked for", r.status_code == 200 and status() == "READY", r.text[:160])
    check("and no stock moved", scalar("select qty_on_hand from stock_balance where company_id = :c and sku = :s", c=company_id, s=SKU) == 5)

    print("\nLogistics: dispatched only when committed")
    r = decide()
    check("marking dispatched before the dispatch order is committed is refused", r.status_code == 400 and "Commit the dispatch" in r.text,
          r.text[:160])

    print("\nThe Production desk reads the ledger")
    from app.models.sales_order import SalesOrder
    from app.services.fulfilment_desk_service import FulfilmentDeskService

    session = SessionLocal()
    stock = FulfilmentDeskService.stock_for(session.get(SalesOrder, order_id), session)
    session.close()
    check("held 3 of 3, plus 2 free in the warehouse", stock[0]["known"] and stock[0]["available"] == 5.0 and stock[0]["short"] is False, str(stock))

    print("\nCancelling")
    r = api("post", "/dispatch/", admin, json={"sales_order_id": order_id, "lines": [{"so_line_id": lines[0]["so_line_id"], "qty": 2}]})
    do_id = data(r)["id"]
    session = SessionLocal()
    from app.services.fulfilment_notice import record_stage_change

    order = session.get(SalesOrder, order_id)
    order.status = "CANCELLED"
    session.commit()
    record_stage_change(order, session, previous="READY", actor_name="test")
    session.close()
    check("cancelling the order cancels its open dispatch order", scalar("select status from dispatch_order where id = :d", d=do_id) == "CANCELLED")
    check("and gives back everything it had reserved", scalar("select qty_reserved from stock_balance where company_id = :c and sku = :s",
                                                              c=company_id, s=SKU) == 0)
    check("on hand is untouched", scalar("select qty_on_hand from stock_balance where company_id = :c and sku = :s", c=company_id, s=SKU) == 5)
    check("the old MongoDB counting wrote nothing", sync_mongo_db["inventory_movements"].count_documents({}) == mongo_before)
finally:
    if previous is None:
        sql("delete from app_setting where key = :k", k=LEDGER_LIVE_SETTING)
    else:
        sql("update app_setting set value = :v where key = :k", v=previous, k=LEDGER_LIVE_SETTING)
    session = SessionLocal()
    if order_id:
        session.execute(text("delete from sales_order_activity where sales_order_id = :o"), {"o": order_id})
        session.execute(text("delete from dispatch_order where sales_order_id = :o"), {"o": order_id})
        session.execute(text("delete from stock_reservation where so_line_id in (select id from sales_order_line where sales_order_id = :o)"), {"o": order_id})
        session.execute(text("delete from sales_order_line where sales_order_id = :o"), {"o": order_id})
        session.execute(text("delete from notifications where entity_id = :o and module ilike '%order%'"), {"o": order_id})
        session.execute(text("delete from sales_order where id = :o"), {"o": order_id})
    if company_id:
        for table in ("inventory_ledger", "stock_balance"):
            session.execute(text(f"delete from {table} where company_id = :c"), {"c": company_id})
    if location_id:
        session.execute(text("delete from locations where id = :l"), {"l": location_id})
    if company_id:
        session.execute(text("delete from companies where id = :c"), {"c": company_id})
    session.commit()
    session.close()
    print("\nSwitch restored; cleaned up.")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
