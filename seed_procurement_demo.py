"""A procurement demo to show the team, kept until it is removed.

Creates a small, clearly labelled cast and walks real requisitions and
purchase orders to different stages, through the API - so everything is
made exactly as the screens would make it:

  People (all "(Demo)", in their own "Demo Department", on mailinator):
    Ravi Employee      Employee         raises requisitions
    Meera Manager      Employee         Ravi's reporting manager
    Sunil Head         Employee         heads the Demo Department
    Priya Finance      Finance          the Finance approval step
    Arvind Director    Director         the Director approval step
    Kiran Buyer        Procurement      works the procurement desk
    Neha Procurement   Procurement Head approves vendors, keeps the matrix
    Anita Accounts     Accounts         approves and pays instalments

  Three approved demo vendors, and five requisitions, one at each stage:
    1. a draft Ravi has not sent yet
    2. sent, waiting on Meera (Waiting on me)
    3. approved, on the desk with quotations from two vendors to compare
    4. a purchase order in its approval chain - Meera signed, Sunil next
    5. an approved purchase order whose advance is with Accounts

The sign-in password for every demo person is TEAM_PASSWORD in
seed_sales_team.py.

Run once; a second run sees the demo already there and stops. To start
again, remove it and run it afresh:

    docker exec -w /app backend_app python seed_procurement_demo.py
    docker exec -w /app backend_app python seed_procurement_demo.py --remove

Note: the Finance and Director steps go to everyone holding those roles,
so while the demo people exist they receive the Finance and Director
approvals of real purchase orders too. Remove the demo before going live.
"""

import argparse
import sys
from datetime import date, timedelta
from io import BytesIO

import requests

from seed_sales_team import BASE, TEAM_PASSWORD, api, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
DEPARTMENT = "Demo Department"
TAG = "[Demo]"
IN_TEN_DAYS = (date.today() + timedelta(days=10)).isoformat()

#: key: (first name, last name, role, reports to key, employee id)
CAST = {
    "head": ("Sunil", "Head (Demo)", "Employee", None, "DEMO-001"),
    "manager": ("Meera", "Manager (Demo)", "Employee", "head", "DEMO-002"),
    "employee": ("Ravi", "Employee (Demo)", "Employee", "manager", "DEMO-003"),
    "finance": ("Priya", "Finance (Demo)", "Finance", None, "DEMO-004"),
    "director": ("Arvind", "Director (Demo)", "Director", None, "DEMO-005"),
    "buyer": ("Kiran", "Buyer (Demo)", "Procurement", None, "DEMO-006"),
    "proc_head": ("Neha", "Procurement (Demo)", "Procurement Head", None, "DEMO-007"),
    "accounts": ("Anita", "Accounts (Demo)", "Accounts", None, "DEMO-008"),
}


def email(key: str) -> str:
    return f"demo.{key.replace('_', '.')}@mailinator.com"


def must(response, what: str) -> dict:
    """The response's data, or stop and say what failed - never carry on
    building a demo on a step that did not happen."""
    if response.status_code >= 400:
        raise SystemExit(f"Stopped at '{what}': {response.status_code} {response.text[:300]}")
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


def upload(token, name):
    content = b"%PDF-1.4\n% demo document\n%%EOF\n"
    r = requests.post(f"{BASE}/attachments", headers={"Authorization": f"Bearer {token}"},
                      files={"file": (name, BytesIO(content), "application/pdf")}, timeout=30)
    body = must(r, f"upload {name}")
    return {"key": body["key"], "name": name, "size": len(content), "type": "application/pdf"}


def settle(user_id):
    """Past the first-login password change, so the demo can sign straight in."""
    from app.database.postgres import SessionLocal
    from app.models.user import User

    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).first()
        user.must_change_password = False
        db.commit()


def give_accounts_payments(db) -> bool:
    """Accounts approve and pay purchase-order instalments; the role was
    never given the permission that does it (purchase_order.pay)."""
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission

    role = db.query(Role).filter(Role.role_name == "Accounts").first()
    perm = db.query(Permission).filter(Permission.permission_name == "purchase_order.pay").first()
    if role is None:
        raise SystemExit("There is no Accounts role.")
    if perm is None:
        perm = Permission(permission_name="purchase_order.pay", module="purchase_order")
        db.add(perm)
        db.flush()
    if db.query(RolePermission).filter_by(role_id=role.id, permission_id=perm.id).first():
        return False
    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()
    return True


def build():
    from app.database.postgres import SessionLocal

    admin = login(*ADMIN)

    if any(u.get("email") == email("employee") for u in rows(api("get", "/users/", admin))):
        print("The demo is already there. Remove it first (--remove) to build it afresh.")
        return 0

    with SessionLocal() as db:
        if give_accounts_payments(db):
            print("Accounts role given purchase_order.pay (approve and pay instalments)")

    roles = {r["role_name"]: r["id"] for r in rows(api("get", "/rbac/roles", admin))}
    missing = {c[2] for c in CAST.values()} - roles.keys()
    if missing:
        raise SystemExit(f"Run seed_departments_and_roles.py first - missing roles: {', '.join(sorted(missing))}")

    companies = rows(api("get", "/companies/", admin, params={"size": 200}))
    company = next((c for c in companies if "synergy global" in c["company_name"].lower()), companies[0])

    dept = must(api("post", "/departments", admin, json={"name": DEPARTMENT, "code": "DEMO"}), "demo department")
    print(f"Department: {DEPARTMENT}")

    ids, tokens = {}, {}
    for key, (first, last, role, boss, code) in CAST.items():
        user = must(api("post", "/users/", admin, json={
            "first_name": first, "last_name": last, "email": email(key), "password": TEAM_PASSWORD,
            "phone_number": "9000000099", "employee_id": code, "role_ids": [roles[role]],
            "company_ids": [company["id"]], "reports_to_id": ids.get(boss) if boss else None,
            "department_id": dept["id"],
        }), f"user {first}")
        ids[key] = user["id"]
        settle(user["id"])
        tokens[key] = login(email(key), TEAM_PASSWORD)
        print(f"  {first} {last:22s} {role:18s} {email(key)}")

    must(api("put", f"/departments/{dept['id']}", admin,
             json={"name": DEPARTMENT, "code": "DEMO", "head_user_id": ids["head"]}), "department head")

    ravi, meera, sunil = tokens["employee"], tokens["manager"], tokens["head"]
    kiran, neha, anita = tokens["buyer"], tokens["proc_head"], tokens["accounts"]

    print("Vendors:")
    vendors = []
    for n, (name, city) in enumerate([("Office Hub Supplies", "Noida"), ("TechWorld Distributors", "Delhi"),
                                      ("PrintPro Solutions", "Gurugram")]):
        docs = {kind: upload(kiran, f"{kind}.pdf") for kind in ("gst", "pan", "bank")}
        v = must(api("post", "/vendors", kiran, json={
            "name": f"{name} (Demo)", "vendor_type": "Distributor", "contact_person": "Sales Desk",
            "mobile": f"98765432{10 + n}", "email": f"demo.vendor{n + 1}@mailinator.com", "city": city,
            "state": "Delhi" if city == "Delhi" else "Uttar Pradesh" if city == "Noida" else "Haryana",
            "pin_code": "110001", "payment_terms": "30 days", "documents": docs,
        }), f"vendor {name}")
        must(api("post", f"/vendors/{v['id']}/action", neha, json={"action": "VERIFY"}), "verify vendor")
        must(api("post", f"/vendors/{v['id']}/action", neha, json={"action": "APPROVE"}), "approve vendor")
        vendors.append(v["id"])
        print(f"  {name} (Demo) - approved by Neha")

    def raise_pr(what, lines, submit):
        return must(api("post", "/purchase-requisitions", ravi, json={
            "purchase_type": "OFFICE", "priority": "NORMAL", "required_by": IN_TEN_DAYS,
            "delivery_location": "Noida office, B-66 Sector 65", "purpose": f"{TAG} {what}",
            "items": lines, "submit": submit,
        }), f"requisition {what}")

    def approve_pr(pr):
        return must(api("post", f"/purchase-requisitions/{pr['id']}/decision", meera,
                        json={"decision": "APPROVE", "remarks": "Needed - go ahead.", "budget_code": "OPEX-DEMO",
                              "budget_available": 500000, "budgeted": True}), "manager approves")

    def quote(pr, vendor, rates):
        items = [{"requisition_item_id": i["id"], "quantity": i["quantity"], "unit_rate": rates[n], "gst_rate": 18,
                  "delivery_days": 5} for n, i in enumerate(pr["items"])]
        return must(api("post", "/vendor-quotations", kiran, json={
            "requisition_id": pr["id"], "vendor_id": vendor, "vendor_reference": f"DEMO-Q-{pr['id']}-{vendor}",
            "quotation_date": date.today().isoformat(), "valid_until": IN_TEN_DAYS, "delivery_days": 5,
            "payment_terms": "30 days", "attachments": [upload(kiran, "quotation.pdf")], "items": items,
        }), "quotation")

    def to_desk(pr, rates_by_vendor):
        pr = approve_pr(pr)
        must(api("post", f"/procurement/requisitions/{pr['id']}/assign", kiran, json={"user_id": ids["buyer"]}),
             "assign to Kiran")
        ws = must(api("post", f"/procurement/requisitions/{pr['id']}/collection", kiran, json={
            "item_vendors": {str(i["id"]): vendors[: len(rates_by_vendor)] for i in pr["items"]},
            "notes": "Asked our usual vendors.",
        }), "request quotations")
        return ws, [quote(ws, vendors[n], rates) for n, rates in enumerate(rates_by_vendor)]

    print("Requisitions:")
    raise_pr("Whiteboard markers for the training room",
             [{"product_name": "Whiteboard marker set", "category": "Stationery", "uom": "Box", "quantity": 10}],
             submit=False)
    print("  1. Whiteboard markers - a draft Ravi has not sent")

    raise_pr("Ergonomic chairs for new joiners",
             [{"product_name": "Ergonomic office chair", "category": "Furniture", "uom": "Nos", "quantity": 6}],
             submit=True)
    print("  2. Office chairs - sent, waiting on Meera")

    pr = raise_pr("Laptops for the new developers",
                  [{"product_name": "Laptop 16GB / 512GB", "category": "IT", "brand": "Dell", "uom": "Nos", "quantity": 4}],
                  submit=True)
    to_desk(pr, [[62000], [59500]])
    print("  3. Laptops - on the procurement desk with two quotations to compare")

    pr = raise_pr("Projectors for the meeting rooms",
                  [{"product_name": "Full HD projector", "category": "IT", "brand": "Epson", "uom": "Nos", "quantity": 2}],
                  submit=True)
    ws, quotes = to_desk(pr, [[55000], [57000]])
    po = must(api("post", "/purchase-orders", kiran, json={
        "quotation_id": quotes[0]["id"], "requisition_item_ids": [i["id"] for i in ws["items"]],
        "payment_term_type": "CREDIT_100", "credit_days": 30, "payment_mode": "BANK_TRANSFER",
    }), "projector PO")
    must(api("post", f"/purchase-orders/{po['id']}/action", kiran, json={"action": "SUBMIT"}), "submit projector PO")
    must(api("post", f"/purchase-orders/{po['id']}/decision", meera,
             json={"decision": "APPROVE", "remarks": "Agreed."}), "Meera approves the PO")
    print(f"  4. Projectors - {po['po_number']} in approval: Meera signed, waiting on Sunil (then Finance)")

    pr = raise_pr("Toner cartridges for the office printers",
                  [{"product_name": "Laser toner cartridge", "category": "Stationery", "brand": "HP", "uom": "Nos",
                    "quantity": 4}], submit=True)
    ws, quotes = to_desk(pr, [[3500]])
    po = must(api("post", "/purchase-orders", kiran, json={
        "quotation_id": quotes[0]["id"], "requisition_item_ids": [i["id"] for i in ws["items"]],
        "payment_term_type": "ADVANCE_CREDIT", "advance_percent": 50, "credit_days": 30, "payment_mode": "BANK_TRANSFER",
    }), "toner PO")
    must(api("post", f"/purchase-orders/{po['id']}/action", kiran, json={"action": "SUBMIT"}), "submit toner PO")
    po = must(api("post", f"/purchase-orders/{po['id']}/decision", meera,
                  json={"decision": "APPROVE", "remarks": "OK."}), "Meera approves the toner PO")
    advance = po["payments"][0]
    url = f"/purchase-orders/{po['id']}/payments/{advance['id']}"
    must(api("put", url, kiran, json={"bank_name": "HDFC Bank", "reference": "Advance for toner"}), "advance details")
    must(api("post", f"{url}/action", kiran, json={"action": "SUBMIT"}), "advance to accounts")
    print(f"  5. Toner - {po['po_number']} approved; the advance is with Anita in Accounts")

    print(f"\nDone. Sign in as any demo person with their address above; the password is TEAM_PASSWORD"
          f" in seed_sales_team.py. Remove it all with --remove.")
    _ = anita, sunil
    return 0


def remove():
    """Everything the demo made, in the order the links between them need."""
    from sqlalchemy import text

    from app.database.mongodb import sync_mongo_db  # noqa: F401 - nothing in Mongo, kept for symmetry
    from app.database.postgres import SessionLocal

    with SessionLocal() as db:
        q = lambda sql, **p: db.execute(text(sql), p)  # noqa: E731
        prs = [r[0] for r in q("select id from purchase_requisition where purpose like :t", t=f"{TAG}%")]
        pos = [r[0] for r in q("select id from purchase_order where requisition_id = any(:p)", p=prs or [-1])]
        vendors = [r[0] for r in q("select id from vendor where name like '%(Demo)'")]
        users = [str(r[0]) for r in q("select id from users where email like 'demo.%@mailinator.com'")]
        print(f"Removing {len(prs)} requisitions, {len(pos)} purchase orders, {len(vendors)} vendors, {len(users)} people")

        p, o, v = prs or [-1], pos or [-1], vendors or [-1]
        q("delete from procurement_activity where requisition_id = any(:p)"
          " or (entity_type = 'purchase_order' and entity_id = any(:o))"
          " or (entity_type = 'vendor' and entity_id = any(:v))", p=p, o=o, v=v)
        q("delete from notifications where module ilike 'procurement%' and entity_id = any(:p)", p=p)
        q("delete from notifications where module ilike 'purchase%' and entity_id = any(:o)", o=o)
        # A PO points at its selection and quotation, which point at the PR.
        q("delete from purchase_order where id = any(:o)", o=o)
        q("delete from vendor_selection where requisition_id = any(:p)", p=p)
        q("delete from quotation_comparison where requisition_id = any(:p)", p=p)
        q("delete from vendor_quotation where requisition_id = any(:p)", p=p)
        q("delete from purchase_requisition where id = any(:p)", p=p)
        q("delete from vendor where id = any(:v)", v=v)
        u = users or ["00000000-0000-0000-0000-000000000000"]
        q("delete from notifications where user_id = any(cast(:u as uuid[]))", u=u)
        q("update department set head_user_id = null where head_user_id = any(cast(:u as uuid[]))", u=u)
        q("update users set reports_to_id = null where reports_to_id = any(cast(:u as uuid[]))", u=u)
        q("delete from user_roles where user_id = any(cast(:u as uuid[]))", u=u)
        q("delete from user_companies where user_id = any(cast(:u as uuid[]))", u=u)
        q("delete from users where id = any(cast(:u as uuid[]))", u=u)
        q("delete from department where name = :d", d=DEPARTMENT)
        db.commit()
    print("Demo removed. (Accounts keeps purchase_order.pay - it is theirs, not the demo's.)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remove", action="store_true", help="delete everything the demo made")
    sys.exit(remove() if parser.parse_args().remove else build())
