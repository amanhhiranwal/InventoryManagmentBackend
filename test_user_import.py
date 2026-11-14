"""Users > Bulk Upload, and people who report to two managers.

A throwaway sheet (CSV) of seven people, tagged, on mailinator:

  * preview writes nothing; the upload then creates them with the role
    their designation maps to, the right department (the sales chart is
    always Sales), and managers found by name, a shortened name or an ID;
  * the same sheet again changes nothing; a blank cell leaves a field as
    it is; a Role column changes a role; Left switches someone off;
  * an Employee ID held by someone with a different name is not used;
  * "also reports to" puts a second manager over someone - both see their
    records, the chart gets both ids, and a loop is refused;
  * nobody without user.bulk_upload may upload.

Everything it creates is removed again.

    docker exec -w /app backend_app python test_user_import.py
"""

import csv
import io
import sys
import uuid

import requests

from seed_sales_team import BASE, TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:5]
T = TAG.upper()

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


HEAD = ["E.Code", "Employee Name", "Email Id", "ContactNo", "Designation", "Department",
        "Reporting Manager", "Also Reports To", "Role", "Company", "Status"]

# A small company: two Directors over a Founder, a sales line under them.
PEOPLE = [
    [f"T{T}01", f"Payal Dir{TAG}", "", "9000000101", "Director", "Management", "NA", "", "", "Synergy Global", "Active"],
    [f"T{T}02", f"Isha Dir{TAG}", "", "9000000102", "Director", "Management", "NA", "", "", "Synergy Global", "Active"],
    [f"T{T}03", f"Nina Fou{TAG}", "", "9000000103", "Founder", "Management", f"Payal Dir{TAG}", f"Isha Dir{TAG}", "", "Synergy Global", "Active"],
    [f"T{T}04", f"Arun Avp{TAG}", "", "9000000104", "AVP Business", "Business", f"Nina Fou{TAG}", "", "", "Synergy Global", "Active"],
    [f"T{T}05", f"Zara Zon{TAG} Kumar", "", "9000000105", "Zonal Head", "Business", f"T{T}04", "", "", "Synergy Global", "Active"],
    [f"T{T}06", f"Amit Are{TAG}", "", "9000000106", "Sr. Area Sales Manager", "Sales", f"Zara Zon{TAG} sir", "", "", "Synergy Global", "Active"],
    [f"T{T}07", f"Lena Lef{TAG}", "", "9000000107", "Area Manager", "Sales", f"Zara Zon{TAG}", "", "", "Synergy Global", "Left"],
]


def sheet(people) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEAD)
    w.writerows(people)
    return out.getvalue().encode()


def upload(token, people, commit, **opts):
    data = {"commit": str(commit).lower(), "test_emails": "true", "send_welcome": "false"}
    data.update({k: str(v).lower() if isinstance(v, bool) else v for k, v in opts.items()})
    return requests.post(f"{BASE}/users/import", headers={"Authorization": f"Bearer {token}"},
                         files={"file": ("people.csv", sheet(people), "text/csv")}, data=data, timeout=120)


created_ids = []

try:
    admin = login(*ADMIN)
    from app.database.postgres import SessionLocal
    from app.models.user import User
    from app.services.hierarchy_service import HierarchyService

    def on_file():
        with SessionLocal() as db:
            found = {}
            for u in db.query(User).filter(User.employee_id.like(f"T{T}%")).all():
                found[u.employee_id] = {
                    "id": str(u.id), "name": f"{u.first_name} {u.last_name}", "email": u.email,
                    "role": [r.role_name for r in u.roles], "designation": u.designation,
                    "department_id": u.department_id, "manager": str(u.reports_to_id) if u.reports_to_id else None,
                    "active": u.is_active, "must_change": u.must_change_password,
                }
            return found

    departments = {d["name"]: d["id"] for d in rows(api("get", "/departments", admin))}

    print("\n1. Preview, then upload")
    r = upload(admin, PEOPLE, commit=False)
    check("the preview answers", r.status_code == 200, r.text[:200])
    preview = r.json()["data"]
    check("it would add six and skip the leaver", preview["counts"].get("create") == 6
          and preview["counts"].get("skip") == 1, str(preview["counts"]))
    check("and has written nothing", on_file() == {}, str(on_file()))

    r = upload(admin, PEOPLE, commit=True, start_password="Demo@Pass123")
    check("the upload answers", r.status_code == 200, r.text[:200])
    people = on_file()
    created_ids = [p["id"] for p in people.values()]
    check("six people are created", len(people) == 6, str(sorted(people)))
    ids = {code: p["id"] for code, p in people.items()}
    p = people.get(f"T{T}06", {})
    check("a Sr. Area Sales Manager is an Area Manager", p.get("role") == ["Area Manager"], str(p))
    check("their designation is kept as written", p.get("designation") == "Sr. Area Sales Manager", str(p))
    check("and they are in Sales whatever the sheet said", p.get("department_id") == departments.get("Sales"), str(p))
    z = people.get(f"T{T}05", {})
    check("a Zonal Head from 'Business' is put in Sales too", z.get("department_id") == departments.get("Sales"), str(z))
    a = people.get(f"T{T}04", {})
    check("an AVP Business is an AVP", a.get("role") == ["AVP"], str(a))
    check("the Directors are in Management", people.get(f"T{T}01", {}).get("department_id") == departments.get("Management"))
    check("logins are on mailinator", all(x["email"].endswith("@mailinator.com") for x in people.values()))
    check("and must change the starting password", all(x["must_change"] for x in people.values()))
    check("a manager given by Employee ID is found", z.get("manager") == ids.get(f"T{T}04"), str(z))
    check("one written as 'Name sir' is found", p.get("manager") == ids.get(f"T{T}05"), str(p))
    check("the Founder reports to Payal", people.get(f"T{T}03", {}).get("manager") == ids.get(f"T{T}01"))
    check("Lena, who left, was not created", f"T{T}07" not in people)

    login(f"{p['email']}", "Demo@Pass123")
    check("a new person can sign in with the starting password", True)

    print("\n2. Two managers over the Founder")
    founder = ids[f"T{T}03"]
    users = {u["id"]: u for u in rows(api("get", "/users/", admin, params={"search": TAG, "size": 50}))}
    check("the Founder lists Isha as also reporting to",
          users.get(founder, {}).get("also_reports_to_ids") == [ids[f"T{T}02"]], str(users.get(founder, {}).get("also_reports_to_ids")))
    with SessionLocal() as db:
        payal = HierarchyService.visible_user_ids({"user_id": ids[f"T{T}01"]}, db)
        isha = HierarchyService.visible_user_ids({"user_id": ids[f"T{T}02"]}, db)
        zara = HierarchyService.visible_user_ids({"user_id": ids[f"T{T}05"]}, db)
    check("Payal sees the Founder and the line below", {founder, ids[f"T{T}06"]} <= payal, str(len(payal)))
    check("so does Isha, through the second line", {founder, ids[f"T{T}06"]} <= isha, str(len(isha)))
    check("and they do not see each other", ids[f"T{T}02"] not in payal and ids[f"T{T}01"] not in isha)
    check("a Zonal Head still sees only their own line", zara == {ids[f"T{T}05"], ids[f"T{T}06"]}, str(zara))

    payal_user = users[ids[f"T{T}01"]]
    r = api("put", f"/users/{ids[f'T{T}01']}", admin, json={
        "first_name": payal_user["first_name"], "last_name": payal_user["last_name"],
        "phone_number": payal_user["phone_number"], "employee_id": payal_user["employee_id"],
        "role_ids": payal_user["role_ids"], "company_ids": payal_user["company_ids"],
        "also_reports_to_ids": [founder],
    })
    check("a loop - Payal also reporting to the Founder below her - is refused", r.status_code == 400, f"{r.status_code} {r.text[:150]}")

    print("\n3. Uploading again: updates, cell by cell")
    r = upload(admin, PEOPLE, commit=False)
    again = r.json()["data"]["counts"]
    check("the same sheet again changes nobody", again.get("create", 0) == 0 and again.get("update", 0) == 0, str(again))

    changed = [list(x) for x in PEOPLE]
    changed[5][4] = "Regional Manager"          # Amit's designation
    changed[5][3] = ""                          # blank phone: leave it
    changed[5][8] = "Zonal Head"                # a Role column changes the role
    changed[3][10] = "Terminated"               # Arun has gone
    changed[4][6] = f"Nina Fou{TAG}"            # Zara now reports to the Founder
    r = upload(admin, changed, commit=True)
    result = r.json()["data"]
    check("the upload reports what it did", r.status_code == 200 and result["counts"].get("update") == 2
          and result["counts"].get("deactivate") == 1, str(result["counts"]))
    people = on_file()
    p = people[f"T{T}06"]
    check("the designation is updated", p["designation"] == "Regional Manager", str(p))
    check("the role is changed by the Role column", p["role"] == ["Zonal Head"], str(p))
    with SessionLocal() as db:
        phone = db.query(User.phone_number).filter(User.employee_id == f"T{T}06").scalar()
    check("a blank phone left the phone as it was", phone == "9000000106", str(phone))
    check("Arun is switched off", people[f"T{T}04"]["active"] is False)
    check("Zara now reports to the Founder", people[f"T{T}05"]["manager"] == founder, str(people[f"T{T}05"]))

    print("\n4. Safety")
    imposter = [[f"T{T}01", f"Someone Else{TAG}", "", "", "Area Manager", "Sales", "NA", "", "", "", "Active"]]
    r = upload(admin, imposter, commit=False)
    row = r.json()["data"]["rows"][0]
    check("an Employee ID held by somebody with another name is not used to match",
          row["action"] != "update" and any("not them" in i for i in row["issues"]), str(row))
    salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
    r = upload(salesperson, PEOPLE, commit=False)
    check("someone without Bulk Upload may not upload", r.status_code == 403, f"{r.status_code}")

    # Someone given Bulk Upload who is not a super admin: an Area Manager.
    # They may not use a sheet to change the role of a person above them.
    from app.database.postgres import SessionLocal as _S
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission

    with _S() as db:
        am_role = db.query(Role).filter(Role.role_name == "Area Manager").one()
        perm = db.query(Permission).filter(Permission.permission_name == "user.bulk_upload").first()
        if perm is None:
            perm = Permission(permission_name="user.bulk_upload", module="user")
            db.add(perm)
            db.flush()
        granted = RolePermission(role_id=am_role.id, permission_id=perm.id)
        db.add(granted)
        db.commit()
        granted_id = granted.id
    try:
        salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
        promote = [list(PEOPLE[2])]          # the Founder
        promote[0][8] = "Area Manager"       # a Role column
        r = upload(salesperson, promote, commit=True)
        row = (r.json().get("data") or {}).get("rows", [{}])[0]
        check("an uploader cannot change the role of someone above them",
              r.status_code == 200 and row.get("action") == "error", str(row)[:200])
        check("the Founder's role is untouched", on_file()[f"T{T}03"]["role"] == ["Founder"], str(on_file()[f"T{T}03"]))
    finally:
        with _S() as db:
            db.query(RolePermission).filter(RolePermission.id == granted_id).delete()
            db.commit()
    r = requests.get(f"{BASE}/users/import/template", headers={"Authorization": f"Bearer {admin}"}, timeout=30)
    check("the template downloads with its columns", r.status_code == 200 and b"Reporting Manager" in r.content
          and b"Also Reports To" in r.content, r.text[:120])
    bad = upload(admin, PEOPLE, commit=False, start_password="short")
    check("a weak starting password is refused", bad.status_code == 400, f"{bad.status_code} {bad.text[:120]}")

except Exception as exc:  # noqa: BLE001 - reported, then cleaned up
    check("the run completes", False, repr(exc)[:200])

finally:
    print("\nClearing up")
    try:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        with SessionLocal() as db:
            ids = [str(r[0]) for r in db.execute(text("select id from users where employee_id like :t"), {"t": f"T{T}%"})]
            u = ids or ["00000000-0000-0000-0000-000000000000"]
            for sql in (
                "delete from user_co_managers where user_id = any(cast(:u as uuid[])) or manager_id = any(cast(:u as uuid[]))",
                "update users set reports_to_id = null where reports_to_id = any(cast(:u as uuid[]))",
                "delete from notifications where user_id = any(cast(:u as uuid[]))",
                "delete from user_roles where user_id = any(cast(:u as uuid[]))",
                "delete from user_companies where user_id = any(cast(:u as uuid[]))",
                "delete from users where id = any(cast(:u as uuid[]))",
            ):
                db.execute(text(sql), {"u": u})
            db.commit()
            left = db.execute(text("select count(*) from users where employee_id like :t"), {"t": f"T{T}%"}).scalar()
        check("nothing is left behind", left == 0, str(left))
    except Exception as exc:  # noqa: BLE001
        print("   cleanup problem:", exc)
        failed.append("cleanup")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
