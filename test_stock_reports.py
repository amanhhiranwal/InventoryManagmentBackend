"""Stock reports and the inventory overview, through the API.

  * every report is limited to the caller's companies and stores;
  * value columns and the valuation report need stock.valuation.view;
  * CSV download needs stock.report.export;
  * stock, valuation, ledger and slow-moving figures add up;
  * unknown reports and groupings are refused.

    docker exec -w /app backend_app python test_stock_reports.py
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
company_ids, user_ids = [], []
SKU = f"RPT-PANEL-{TAG}"


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"RPT Test {letter} {TAG}", "company_code": f"RP{letter}{TAG}",
        "email": f"rpt.{letter.lower()}.{TAG.lower()}@mailinator.com", "phone_number": "9800000051",
        "address_line_1": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India", "postal_code": "201301",
    })
    company_ids.append(data(r)["id"])
    return data(r)["id"]


def person(label, permissions, company_list):
    email = f"rpt.{label}.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "RPT", "last_name": f"{label} {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-RPT-{label}-{TAG}", "phone_number": "9800000052", "role_ids": [], "company_ids": company_list,
    })
    uid = data(r)["id"]
    user_ids.append(uid)
    rid = str(uuid.uuid4())
    sql("insert into roles (id, role_name, description, created_at, updated_at) values (:i, :n, 'rpt test', now(), now())",
        i=rid, n=f"RPT Test {label} {TAG}")
    for name in permissions:
        sql("insert into role_permissions (id, role_id, permission_id, created_at, updated_at) "
            "select :i, :r, id, now(), now() from permissions where permission_name = :p", i=str(uuid.uuid4()), r=rid, p=name)
    api("put", f"/users/{uid}/role", admin, json={"role_ids": [rid], "company_ids": company_list})
    sql("update users set must_change_password = false where id = :id", id=uid)
    return login(email, TEAM_PASSWORD)


def stock_in(company_id, quantity, cost):
    store = scalar("select id::text from locations where company_id=:c", c=company_id)
    bin_id = scalar("select id::text from warehouse_bins where warehouse_id=:w and code='STORE'", w=store)
    session = SessionLocal()
    posting.post(session, posting.Posting(
        source_doc_type="TEST", source_doc_id=f"RPT-{TAG}-{company_id}", source_doc_number=None, movement_date=date.today(),
        movements=[posting.Movement(company_id=uuid.UUID(company_id), warehouse_id=uuid.UUID(store), bin_id=uuid.UUID(bin_id),
                                    sku=SKU, product_name=f"RPT Panel {TAG}", stock_status=UNRESTRICTED, quantity=Decimal(quantity),
                                    movement_type=OPENING_BALANCE, source_line_id="1", unit_cost=Decimal(cost))]))
    session.commit()
    session.close()


def report(token, kind, **params):
    return api("get", f"/stock/reports/{kind}", token, params=params)


try:
    a, b = company("A"), company("B")
    for cid in (a, b):
        InventoryService.items_col.insert_one({"name": f"RPT Panel {TAG}", "serial_number": "", "product_type_code": "TEST",
                                               "category": "Test", "company_id": cid,
                                               "attributes": {"model_number": SKU, "specification": "t", "instock": 0},
                                               "rpt_test_tag": TAG})
    stock_in(a, 10, 100)
    stock_in(b, 5, 300)
    viewer = person("viewer", ["stock.read"], [a])
    finance = person("finance", ["stock.read", "stock.valuation.view", "stock.report.export"], [a])

    print("\nScope and permissions")
    r = report(viewer, "stock", group_by="product")
    rows = [x for x in data(r)["rows"] if x.get("sku") == SKU]
    check("the stock report shows only the caller's company", r.status_code == 200 and len(rows) == 1 and rows[0]["on_hand"] == 10,
          str(rows)[:200])
    check("without valuation rights there is no value column",
          "value" not in {c["key"] for c in data(r)["columns"]} and "value" not in rows[0])
    check("the valuation report is refused without valuation rights", report(viewer, "valuation").status_code == 403)
    check("CSV is refused without export rights", report(viewer, "stock", format="csv").status_code == 403)
    check("an unknown report is not found", report(viewer, "everything").status_code == 404)
    check("an unknown grouping is refused", report(viewer, "stock", group_by="planet").status_code == 400)
    check("another company's store is refused", report(viewer, "stock", company_id=b).status_code == 403)

    print("\nFigures")
    r = report(finance, "valuation")
    mine = [x for x in data(r)["rows"] if x["sku"] == SKU]
    check("valuation: 10 units at Rs 100 is Rs 1,000, the other company's stock left out",
          r.status_code == 200 and len(mine) == 1 and mine[0]["value"] == 1000 and mine[0]["avg_cost"] == 100, str(mine)[:200])
    r = report(finance, "stock", group_by="warehouse")
    check("stock by warehouse carries value for finance",
          any(x.get("value") == 1000 for x in data(r)["rows"]), str(data(r)["rows"])[:200])
    r = report(finance, "ledger", date_from=date.today().isoformat())
    check("the ledger report lists the movement", sum(1 for x in data(r)["rows"] if x["sku"] == SKU) == 1)
    r = report(finance, "slow_moving", days=1)
    idle = [x for x in data(r)["rows"] if x["sku"] == SKU]
    check("stock that never went out is listed as dead stock", len(idle) == 1 and idle[0]["state"].startswith("Dead"), str(idle)[:200])
    r = report(finance, "stock", format="csv")
    check("finance downloads the CSV", r.status_code == 200 and r.headers.get("content-type", "").startswith("text/csv")
          and "Total On Hand" in r.text and SKU in r.text, r.text[:120])

    print("\nOverview")
    r = api("get", "/stock/overview", viewer)
    check("the overview answers with buckets, work waiting and recent moves",
          r.status_code == 200 and {"buckets", "pending", "recent"} <= set(data(r)), r.text[:160])
    check("and no stock value for someone without valuation rights", data(r)["value"] is None)
    check("finance sees the value of their company's stock", data(api("get", "/stock/overview", finance))["value"] == 1000)
finally:
    InventoryService.items_col.delete_many({"rpt_test_tag": TAG})
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
