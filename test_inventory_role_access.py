"""An Inventory-role user and the products they see.

Products belong to companies, and anyone but a super admin sees only the
companies on their own profile:
  * an Inventory user with no company sees no products, is told there is
    no company to list (the companies list is empty, not "every company"),
    and cannot add one - refused with a reason;
  * once a company is on their profile, they see that company's products -
    the same ones a super admin sees for it - and can add and edit them;
  * a salesperson without the inventory permissions still cannot.

    docker exec -w /app backend_app python test_inventory_role_access.py
"""

import sys
import uuid

from sqlalchemy import text

from app.database.mongodb import sync_mongo_db
from app.database.postgres import SessionLocal
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, email_for, login, rows

TAG = uuid.uuid4().hex[:6].upper()
EMAIL = f"inventory.access.{TAG.lower()}@mailinator.com"
NAME = f"Role Access Panel {TAG}"

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)

roles = {r["role_name"]: r for r in rows(api("get", "/roles/", admin))}
companies = rows(api("get", "/inventory/companies", admin))
types = rows(api("get", "/product-types", admin))
units = rows(api("get", "/inventory/units", admin))

if "Inventory" not in roles or not companies or not types or not units:
    print("Run the seeds first: an Inventory role, a company, product types and units are needed.")
    sys.exit(1)

company = companies[0]
kind = types[0]


def product(serial, **extra):
    return {
        "name": NAME,
        "serial_number": serial,
        "product_type_code": kind["code"],
        "category": kind["category"],
        "attributes": {
            "model_number": f"RAP-{TAG}",
            "specification": "Access test",
            "rate": 1000,
            "unit": str(units[0]),
            "instock": 1,
            "case_size": 1,
        },
        **extra,
    }


def listed(token):
    return api("get", "/inventory/items", token, params={"page": 1, "size": 500}).json()


user_id = None

try:
    created = api("post", "/users/", admin, json={
        "first_name": "Inventory",
        "last_name": f"Access {TAG}",
        "email": EMAIL,
        "password": TEAM_PASSWORD,
        "employee_id": f"SYN-INV-{TAG}",
        "phone_number": "9800000011",
        "role_ids": [str(roles["Inventory"]["id"])],
        "company_ids": [],
    })
    check("an Inventory user is created with no company", created.status_code < 400, f"{created.status_code} {created.text[:160]}")
    body = created.json()
    user_id = (body.get("data") or body).get("id")
    # A new user chooses their own password first; the test has done so.
    session = SessionLocal()
    session.execute(text("update users set must_change_password = false where id = :id"), {"id": user_id})
    session.commit()
    session.close()
    person = login(EMAIL, TEAM_PASSWORD)

    print("\nWith no company on the profile")
    seen = listed(person)
    check("no products are listed", seen.get("total") == 0 and seen.get("data") == [], str(seen.get("total")))
    scope = api("get", "/inventory/companies", person).json()
    check("the companies list says there are none, not every company",
          scope.get("data") == [] and scope.get("unrestricted") is False, str(scope))
    r = api("post", "/inventory/items", person, json=product(f"RAP-{TAG}-0"))
    check("adding a product is refused, saying why", r.status_code == 400 and "not assigned to any company" in r.text,
          f"{r.status_code} {r.text[:160]}")

    print("\nWith the company on the profile")
    r = api("put", f"/users/{user_id}/role", admin, json={
        "role_ids": [str(roles["Inventory"]["id"])], "company_ids": [company["id"]],
    })
    check("a super admin adds the company", r.status_code < 400, f"{r.status_code} {r.text[:160]}")
    seen = listed(person)
    for_company = api("get", "/inventory/items", admin, params={"company_id": company["id"], "page": 1, "size": 500}).json()
    check("the user now sees that company's products, as a super admin does",
          seen.get("total") == for_company.get("total") and seen.get("total", 0) > 0,
          f"{seen.get('total')} vs {for_company.get('total')}")

    r = api("post", "/inventory/items", person, json=product(f"RAP-{TAG}-1"))
    check("they can add a product", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    added = sync_mongo_db["inventory_items"].find_one({"name": NAME})
    check("filed under their company", added is not None and added.get("company_id") == company["id"], str(added and added.get("company_id")))

    if added:
        r = api("put", f"/inventory/items/{added['_id']}", person,
                json={**product(f"RAP-{TAG}-1"), "attributes": {**product("")["attributes"], "rate": 1500}})
        check("and edit it", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
        check("the change is saved", sync_mongo_db["inventory_items"].find_one({"_id": added["_id"]})["attributes"]["rate"] == 1500)

    print("\nWithout the inventory permissions")
    r = api("post", "/inventory/items", salesperson, json=product(f"RAP-{TAG}-2"))
    check("a salesperson cannot add a product", r.status_code == 403, str(r.status_code))
finally:
    sync_mongo_db["inventory_items"].delete_many({"name": NAME})
    if user_id:
        session = SessionLocal()
        for table in ("user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :id"), {"id": user_id})
        session.execute(text("delete from users where id = :id"), {"id": user_id})
        session.commit()
        session.close()
    print("   removed the test user and product")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
