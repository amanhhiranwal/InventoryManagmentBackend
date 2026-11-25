"""Companies, warehouses, bins and who may work in which warehouse.

Two companies each get a warehouse called Noida; they must stay two
separate warehouses. Every warehouse is created with its store, quality
check and rejected-goods bins, an office gets none, and only a super
admin changes bins. A person can be given warehouses only of companies on
their profile, sees only those, and loses them when the company is taken
off their profile. Checks run on the server, through the API.

    docker exec -w /app backend_app python test_warehouse_access.py
"""

import sys
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database.postgres import SessionLocal
from app.services.company_scope_service import CompanyScopeService
from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, email_for, login, rows

TAG = uuid.uuid4().hex[:6].upper()
passed, failed = [], []


def session_scalar(statement, **params):
    session = SessionLocal()
    try:
        return session.execute(text(statement), params).scalar()
    finally:
        session.close()


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
roles = {r["role_name"]: r for r in rows(api("get", "/roles/", admin))}

if "Inventory" not in roles:
    print("Run the seeds first: an Inventory role is needed.")
    sys.exit(1)

company_ids, location_ids, user_id = [], [], None


def company(letter):
    r = api("post", "/companies/", admin, json={
        "company_name": f"WH Test {letter} {TAG}",
        "company_code": f"WT{letter}{TAG}",
        "email": f"wh.{letter.lower()}.{TAG.lower()}@mailinator.com",
        "phone_number": "9800000021",
        "address_line_1": "Sector 62",
        "city": "Noida",
        "state": "Uttar Pradesh",
        "country": "India",
        "postal_code": "201301",
    })
    assert r.status_code < 400, r.text
    found = data(r)
    company_ids.append(found["id"])
    return found["id"]


def location(company_id, code, name="Noida", kind="WAREHOUSE"):
    r = api("post", "/locations/", admin, json={
        "company_id": company_id,
        "location_name": name,
        "location_code": f"{code}{TAG}",
        "email": f"loc.{code.lower()}.{TAG.lower()}@mailinator.com",
        "city": "Noida",
        "location_type": kind,
    })
    assert r.status_code < 400, r.text
    found = data(r)
    location_ids.append(found["id"])
    return found


def bins(location_id, token=None):
    return api("get", f"/locations/{location_id}/bins", token or admin)


def set_access(company_list, warehouses=None):
    body = {"role_ids": [str(roles["Inventory"]["id"])], "company_ids": company_list}
    if warehouses is not None:
        body["warehouse_ids"] = warehouses
    return api("put", f"/users/{user_id}/role", admin, json=body)


def mine(token, **params):
    return api("get", "/locations/warehouses/mine", token, params=params)


try:
    a, b = company("A"), company("B")
    noida_a = location(a, "NA")
    noida_b = location(b, "NB")
    office_a = location(a, "OA", name="Head Office", kind="OFFICE")

    print("\nTwo companies, two warehouses both called Noida")
    check("they are two separate warehouses", noida_a["id"] != noida_b["id"] and noida_a["company_id"] != noida_b["company_id"])
    check("saved with their type", noida_a.get("location_type") == "WAREHOUSE" and office_a.get("location_type") == "OFFICE",
          f"{noida_a.get('location_type')} {office_a.get('location_type')}")

    print("\nStandard bins")
    held = data(bins(noida_a["id"]))
    check("a warehouse is created with STORE, QC and REJECT bins",
          sorted((x["code"], x["bin_type"]) for x in held) == [("QC", "QC"), ("REJECT", "REJECT"), ("STORE", "STORE")], str(held))
    check("the other company's Noida has its own three",
          len(data(bins(noida_b["id"]))) == 3 and {x["warehouse_id"] for x in data(bins(noida_b["id"]))} == {noida_b["id"]})
    r = bins(office_a["id"])
    check("an office has no bins", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

    print("\nChanging bins")
    r = api("post", f"/locations/{noida_a['id']}/bins", admin, json={"code": "a-01", "name": "Rack A shelf 1", "rack": "A", "shelf": "1"})
    added = data(r)
    check("a super admin adds a bin; the code is upper-cased", r.status_code == 200 and added.get("code") == "A-01", f"{r.status_code} {r.text[:120]}")
    r = api("post", f"/locations/{noida_a['id']}/bins", admin, json={"code": "A-01", "name": "Again"})
    check("the same code twice in one warehouse is refused", r.status_code == 400 and "already exists" in r.text, f"{r.status_code} {r.text[:120]}")
    r = api("post", f"/locations/{noida_b['id']}/bins", admin, json={"code": "A-01", "name": "Other company"})
    check("the same code in another warehouse is fine", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    r = api("post", f"/locations/{noida_a['id']}/bins", admin, json={"code": "X1", "name": "Bad", "bin_type": "SHELF"})
    check("an unknown bin type is refused", r.status_code == 422, str(r.status_code))
    qc = next(x for x in held if x["code"] == "QC")
    r = api("put", f"/locations/{noida_a['id']}/bins/{qc['id']}", admin, json={"is_active": False})
    check("the only QC bin cannot be switched off", r.status_code == 400 and "only active QC" in r.text, f"{r.status_code} {r.text[:120]}")
    r = api("put", f"/locations/{noida_a['id']}/bins/{added['id']}", admin, json={"is_active": False})
    check("an extra store bin can be switched off", r.status_code == 200 and data(r)["is_active"] is False, f"{r.status_code} {r.text[:120]}")
    r = api("put", f"/locations/{noida_b['id']}/bins/{added['id']}", admin, json={"name": "Moved"})
    check("a bin is not found through another warehouse's address", r.status_code == 404, str(r.status_code))
    r = api("post", f"/locations/{noida_a['id']}/bins", salesperson, json={"code": "S1", "name": "Sales try"})
    check("a non-super-admin cannot add a bin", r.status_code == 403, str(r.status_code))

    print("\nGiving a person warehouses")
    email = f"warehouse.access.{TAG.lower()}@mailinator.com"
    r = api("post", "/users/", admin, json={
        "first_name": "Warehouse", "last_name": f"Access {TAG}", "email": email, "password": TEAM_PASSWORD,
        "employee_id": f"SYN-WH-{TAG}", "phone_number": "9800000022",
        "role_ids": [str(roles["Inventory"]["id"])], "company_ids": [a],
    })
    assert r.status_code < 400, r.text
    user_id = data(r)["id"]
    session = SessionLocal()
    session.execute(text("update users set must_change_password = false where id = :id"), {"id": user_id})
    session.commit()
    session.close()
    person = login(email, TEAM_PASSWORD)

    # Each company is one place: with no particular grant, a person works in
    # their own company's stores - and never another company's.
    seen = data(mine(person))
    check("with no grant they see their own company's stores", seen and all(w["company_id"] == a for w in seen), str(seen))
    check("and none of another company's", not any(w["company_id"] == b for w in seen), str(seen))
    check("and never an office", office_a["id"] not in {w["id"] for w in seen}, str(seen))
    r = set_access([a], [noida_b["id"]])
    check("a warehouse of a company not on the profile is refused", r.status_code == 400 and "not on this person's profile" in r.text,
          f"{r.status_code} {r.text[:140]}")
    r = set_access([a], [office_a["id"]])
    check("an office cannot be granted as a warehouse", r.status_code == 404, f"{r.status_code} {r.text[:120]}")
    r = set_access([a], [str(uuid.uuid4())])
    check("an unknown warehouse is refused", r.status_code == 404, str(r.status_code))
    r = set_access([a], [noida_a["id"]])
    check("their own company's warehouse is granted", r.status_code == 200 and data(r).get("warehouse_ids") == [noida_a["id"]],
          f"{r.status_code} {r.text[:160]}")
    r = set_access([a], [noida_a["id"]])
    check("saving the same grant again keeps one grant", r.status_code == 200 and data(r).get("warehouse_ids") == [noida_a["id"]])

    seen = data(mine(person))
    # A store given to particular people or roles is kept for them; one
    # given to nobody stays open to its company.
    store_a = session_scalar("select id::text from locations where company_id = :c and location_code like '%-STORE'", c=a)
    check("they see that warehouse and their company's open store", {w["id"] for w in seen} == {noida_a["id"], store_a}, str(seen))

    other_role = next(r for name, r in roles.items() if name != "Inventory")
    r = api("put", f"/rbac/roles/{other_role['id']}/warehouses", admin, json={"warehouse_ids": [store_a]})
    check("Roles & Access gives the store to another role", r.status_code == 200 and any(x["id"] == store_a and x["given"] for x in data(r)), r.text[:160])
    check("so it is kept for that role: this person no longer sees it", store_a not in {w["id"] for w in data(mine(person))})
    r = api("put", f"/rbac/roles/{roles['Inventory']['id']}/warehouses", person, json={"warehouse_ids": [store_a]})
    check("only a super admin gives stores to roles", r.status_code == 403, str(r.status_code))
    r = api("put", f"/rbac/roles/{roles['Inventory']['id']}/warehouses", admin, json={"warehouse_ids": [store_a]})
    check("given to their own role too, they see it again", r.status_code == 200 and store_a in {w["id"] for w in data(mine(person))})
    for role in (other_role, roles["Inventory"]):
        api("put", f"/rbac/roles/{role['id']}/warehouses", admin, json={"warehouse_ids": []})
    check("taken off both roles, it is open to the company again", store_a in {w["id"] for w in data(mine(person))})
    r = mine(person, company_id=b)
    check("asking for another company's warehouses is refused", r.status_code == 403, str(r.status_code))
    everyone = {w["id"] for w in data(mine(admin))}
    check("a super admin sees both Noida warehouses", {noida_a["id"], noida_b["id"]} <= everyone)
    check("and no offices", office_a["id"] not in everyone)

    print("\nThe server's own check")
    session = SessionLocal()
    caller = {"user_id": user_id, "is_super_admin": False}

    def refused(warehouse_id):
        try:
            CompanyScopeService.assert_warehouse_allowed(caller, warehouse_id, session)
        except HTTPException as error:
            return error.status_code
        return None

    check("their warehouse passes", refused(noida_a["id"]) is None)
    check("the other company's Noida is reported not found", refused(noida_b["id"]) == 404)
    check("an office is reported not found", refused(office_a["id"]) == 404)
    check("a malformed id is reported not found", refused("not-a-uuid") == 404)
    from app.models.user import User
    from app.services.warehouse_service import WarehouseService

    editor_id = session.execute(text("select id from users where email = :e"), {"e": email_for("am_north_1")}).scalar()
    editor = {"user_id": str(editor_id), "is_super_admin": False}
    target = session.query(User).filter(User.id == uuid.UUID(user_id)).first()

    def grant(warehouses, companies):
        try:
            WarehouseService.set_user_warehouses(target, warehouses, companies, editor, session)
            session.flush()
            held = sorted(WarehouseService.warehouse_ids_of(target, session))
            session.rollback()
            return held
        except HTTPException as error:
            session.rollback()
            return error.status_code

    check("an editor who does not work in a warehouse keeps someone's existing grant there",
          grant([noida_a["id"]], [a]) == [noida_a["id"]])
    check("but cannot hand out a new one there", grant([noida_a["id"], noida_b["id"]], [a, b]) == 403)
    check("an existing grant goes with its company when the company is removed", grant([noida_a["id"]], []) == [])

    try:
        session.execute(text("insert into user_warehouses (id, user_id, warehouse_id, created_at, updated_at) "
                             "values (:i, :u, :w, now(), now())"), {"i": str(uuid.uuid4()), "u": user_id, "w": noida_a["id"]})
        session.commit()
        check("the database refuses a duplicate grant", False, "insert succeeded")
    except IntegrityError:
        session.rollback()
        check("the database refuses a duplicate grant", True)
    session.close()

    print("\nTaking the company away")
    r = set_access([])
    check("removing the company drops its warehouses", r.status_code == 200 and data(r).get("warehouse_ids") == [], f"{r.text[:160]}")
    check("they see no warehouses", data(mine(person)) == [])
    r = set_access([a])
    check("giving the company back does not bring the warehouse back on its own", data(r).get("warehouse_ids") == [])
    r = set_access([a], [noida_a["id"]])
    check("leaving warehouse_ids out keeps the grant", data(set_access([a])).get("warehouse_ids") == [noida_a["id"]])

    print("\nThrough the Edit User form (PUT /users/{id})")
    form = {
        "first_name": "Warehouse", "last_name": f"Access {TAG}", "phone_number": "9800000022",
        "employee_id": f"SYN-WH-{TAG}", "role_ids": [str(roles["Inventory"]["id"])], "company_ids": [a],
    }
    r = api("put", f"/users/{user_id}", admin, json={**form, "warehouse_ids": [noida_b["id"]]})
    check("the form refuses another company's warehouse too", r.status_code == 400, f"{r.status_code} {r.text[:140]}")
    r = api("put", f"/users/{user_id}", admin, json=form)
    check("a form that does not send warehouses keeps them", data(r).get("warehouse_ids") == [noida_a["id"]], r.text[:160])
    r = api("put", f"/users/{user_id}", admin, json={**form, "warehouse_ids": []})
    check("the form can clear them", r.status_code == 200 and data(r).get("warehouse_ids") == [], r.text[:160])
    r = api("put", f"/users/{user_id}", admin, json={**form, "warehouse_ids": [noida_a["id"]]})
    check("and grant them", data(r).get("warehouse_ids") == [noida_a["id"]], r.text[:160])
    r = api("put", f"/users/{user_id}", salesperson, json={**form, "warehouse_ids": [noida_b["id"]]})
    check("a user without user.update cannot change them", r.status_code == 403, str(r.status_code))

    print("\nAn office turned into a warehouse")
    r = api("put", f"/locations/{office_a['id']}", admin, json={"location_type": "WAREHOUSE"})
    check("is saved as a warehouse", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    check("and gets its standard bins", len(data(bins(office_a["id"]))) == 3)
finally:
    session = SessionLocal()
    if user_id:
        for table in ("user_warehouses", "user_roles", "user_companies"):
            session.execute(text(f"delete from {table} where user_id = :id"), {"id": user_id})
        session.execute(text("delete from users where id = :id"), {"id": user_id})
    for location_id in location_ids:
        session.execute(text("delete from locations where id = :id"), {"id": location_id})
    for company_id in company_ids:
        session.execute(text("delete from user_companies where company_id = :id"), {"id": company_id})
        session.execute(text("delete from companies where id = :id"), {"id": company_id})
    session.commit()
    left = session.execute(text("select count(*) from warehouse_bins where warehouse_id::text = any(:ids)"),
                           {"ids": location_ids or [str(uuid.uuid4())]}).scalar()
    session.close()
    print(f"\nCleaned up; bins left behind: {left}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
