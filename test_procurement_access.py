"""Who may raise a requisition, and an approval matrix that can be edited.

1. The PR approval matrix is no longer fixed. A super admin saves whole
   bands - each an upper amount and its approvers in order, from the four
   form steps or any role - and a purchase order's chain follows them.
   Bad bands are refused on the server, and nobody without
   procurement_masters.manage may save.

2. My Requisitions is a tick on Roles & Access (requisition.create):
   * a new role starts with it - any role may raise a requisition;
   * without it, raising, editing or submitting one is refused on the
     server, the form is told so, and the page leaves the sidebar;
   * an approver keeps the page for "Waiting on me" even without it,
     but still may not raise one.

A throwaway role and users are made and removed; the matrix is put back
exactly as it was found.

    docker exec -w /app backend_app python test_procurement_access.py
"""

import sys
import uuid

from seed_sales_team import TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
PASSWORD = "Synergy@123"
TAG = uuid.uuid4().hex[:6]

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def settled(user_id):
    """Past the first-login password change, which has its own suite."""
    from app.database.postgres import SessionLocal
    from app.models.user import User

    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if user is not None:
            user.must_change_password = False
            db.commit()


def titles(token):
    tree = rows(api("get", "/menus/sidebar", token))
    return {child["title"] for item in tree for child in (item.get("children") or [])} | {
        item["title"] for item in tree
    }


created_users, created_role, created_prs = [], None, []
original_bands = None

try:
    admin = login(*ADMIN)

    # ------------------------------------------------------------------
    print("\n1. The approval matrix can be edited")
    matrix = api("get", "/procurement/approval-matrix", admin).json()["data"]
    original_bands = [{"upto": b["upto"], "steps": b["steps"]} for b in matrix["bands"]]
    options = {o["value"] for o in matrix["options"]}
    check("the four form steps are offered",
          {"REPORTING_MANAGER", "DEPARTMENT_HEAD", "FINANCE", "DIRECTOR"} <= options, str(sorted(options)))
    check("and so are roles, such as the CEO", "ROLE:CEO" in options, str(sorted(options)))

    bands = [
        {"upto": 25000, "steps": ["REPORTING_MANAGER"]},
        {"upto": 200000, "steps": ["REPORTING_MANAGER", "FINANCE"]},
        {"upto": 1000000, "steps": ["DEPARTMENT_HEAD", "REPORTING_MANAGER", "ROLE:CEO"]},
        {"upto": None, "steps": ["REPORTING_MANAGER", "DEPARTMENT_HEAD", "FINANCE", "ROLE:CEO", "DIRECTOR"]},
    ]
    r = api("put", "/procurement/approval-matrix", admin, json={"bands": bands})
    check("a super admin saves four bands, reordered, with the CEO in them", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    saved = api("get", "/procurement/approval-matrix", admin).json()["data"]
    check("they read back exactly as saved",
          [{"upto": b["upto"], "steps": b["steps"]} for b in saved["bands"]]
          == [{"upto": float(b["upto"]) if b["upto"] else None, "steps": b["steps"]} for b in bands],
          str(saved["bands"]))
    check("with the CEO named in plain words", "CEO" in saved["bands"][2]["labels"], str(saved["bands"][2]))

    from app.database.postgres import SessionLocal
    from app.services.procurement import approval_chain, masters
    from app.services.procurement.common import role_holders

    with SessionLocal() as db:
        check("a 20,000 order takes band 1", masters.steps_for_amount(20000, db) == ["REPORTING_MANAGER"])
        check("a 25,000 order is still band 1 (the ceiling is inclusive)",
              masters.steps_for_amount(25000, db) == ["REPORTING_MANAGER"])
        check("a 5,00,000 order takes band 3, in its new order",
              masters.steps_for_amount(500000, db) == ["DEPARTMENT_HEAD", "REPORTING_MANAGER", "ROLE:CEO"])
        check("a 50,00,000 order takes the last band", len(masters.steps_for_amount(5000000, db)) == 5)
        ids, note = approval_chain.approvers_for("ROLE:CEO", requester_id=None, department_id=None,
                                                 exclude=set(), db=db)
        ceos = role_holders("CEO", db)
        check("the CEO step goes to the people holding the CEO role",
              bool(ceos) and sorted(ids) == sorted(ceos), f"{ids} vs {ceos}")

    def refused(label, payload, token=admin, status=400):
        r = api("put", "/procurement/approval-matrix", token, json=payload)
        check(label, r.status_code == status, f"{r.status_code} {r.text[:160]}")
        return r

    refused("ceilings that do not rise are refused",
            {"bands": [{"upto": 50000, "steps": ["FINANCE"]}, {"upto": 40000, "steps": ["FINANCE"]},
                       {"upto": None, "steps": ["FINANCE"]}]})
    refused("a band with no approver is refused",
            {"bands": [{"upto": 50000, "steps": []}, {"upto": None, "steps": ["FINANCE"]}]})
    refused("an approver that does not exist is refused",
            {"bands": [{"upto": None, "steps": ["ROLE:Nobody Holds This"]}]})
    refused("the same approver twice in a band is refused",
            {"bands": [{"upto": None, "steps": ["FINANCE", "FINANCE"]}]})
    refused("a last band with an upper amount is refused",
            {"bands": [{"upto": 50000, "steps": ["FINANCE"]}]})
    refused("no bands at all is refused", {"bands": []})

    salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
    refused("someone without Masters may not save it", {"bands": bands}, token=salesperson, status=403)

    one = api("put", "/procurement/approval-matrix", admin, json={"bands": [{"upto": None, "steps": ["ROLE:CEO"]}]})
    check("a single band - one approver for every amount - is allowed", one.status_code == 200, one.text[:160])

    legacy = api("put", "/procurement/approval-matrix", admin, json={"limits": [60000, 600000]})
    check("two thresholds on their own still work, with the form's approvers",
          legacy.status_code == 200 and [b["upto"] for b in legacy.json()["data"]["bands"]] == [60000, 600000, None],
          legacy.text[:200])

    # ------------------------------------------------------------------
    print("\n2. Raising a requisition is a tick on Roles & Access")
    r = api("post", "/rbac/roles", admin, json={"role_name": f"PR Access {TAG}", "description": "test role"})
    check("a super admin creates a role", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    created_role = r.json().get("data", r.json())["id"]

    held = rows(api("get", f"/rbac/roles/{created_role}/permissions", admin))
    perm = next((p for p in held if (p.get("permission_name") or p.get("name")) == "requisition.create"), None)
    check("a new role may raise requisitions from the start", perm is not None, str(held)[:200])

    company = rows(api("get", "/companies/", admin, params={"size": 5}))[0]["id"]

    def make_user(label, manager_id=None):
        r = api("post", "/users/", admin, json={
            "first_name": label, "last_name": TAG, "email": f"pra.{label.lower()}.{TAG}@mailinator.com",
            "password": PASSWORD, "phone_number": "9000000003", "employee_id": f"EMP-PRA-{label.upper()}-{TAG}",
            "role_ids": [created_role], "company_ids": [company], "reports_to_id": manager_id,
        })
        if r.status_code >= 400:
            raise RuntimeError(f"user {label}: {r.status_code} {r.text[:200]}")
        uid = r.json().get("data", r.json())["id"]
        created_users.append(uid)
        settled(uid)
        return uid, f"pra.{label.lower()}.{TAG}@mailinator.com"

    worker_id, worker_email = make_user("Worker")
    worker = login(worker_email, PASSWORD)
    meta = api("get", "/purchase-requisitions/meta", worker).json()["data"]
    check("the form says they may raise one", meta["me"]["can_raise"] is True, str(meta["me"]))
    check("My Requisitions is in their sidebar", "My Requisitions" in titles(worker))
    attempt = api("post", "/purchase-requisitions", worker, json={})
    if attempt.status_code == 200:
        created_prs.append(attempt.json()["data"]["id"])
    check("they save a draft requisition", attempt.status_code == 200, f"{attempt.status_code} {attempt.text[:160]}")

    pid = (perm or {}).get("id")
    r = api("delete", f"/rbac/roles/{created_role}/permissions/{pid}", admin)
    check("a super admin unticks My Requisitions for the role", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    worker = login(worker_email, PASSWORD)
    attempt = api("post", "/purchase-requisitions", worker, json={})
    check("now raising one is refused on the server", attempt.status_code == 403, f"{attempt.status_code}")
    check("saying who can change that", "super admin" in attempt.text, attempt.text[:200])
    meta = api("get", "/purchase-requisitions/meta", worker).json()["data"]
    check("the form is told they may not", meta["me"]["can_raise"] is False, str(meta["me"]))
    check("and My Requisitions leaves their sidebar", "My Requisitions" not in titles(worker))
    check("the page's data still loads for them, empty",
          api("get", "/purchase-requisitions/mine", worker).status_code == 200)

    print("\n   ...but an approver keeps the page")
    make_user("Report", manager_id=worker_id)
    check("with someone reporting to them, My Requisitions is back for Waiting on me",
          "My Requisitions" in titles(worker))
    check("and their approvals load", api("get", "/purchase-requisitions/approvals", worker).status_code == 200)
    attempt = api("post", "/purchase-requisitions", worker, json={})
    check("yet raising one is still refused", attempt.status_code == 403, f"{attempt.status_code}")

    r = api("post", f"/rbac/roles/{created_role}/permissions/{pid}", admin)
    check("ticking it again gives it back", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    worker = login(worker_email, PASSWORD)
    attempt = api("post", "/purchase-requisitions", worker, json={})
    if attempt.status_code == 200:
        created_prs.append(attempt.json()["data"]["id"])
    check("and they may raise one again", attempt.status_code == 200, f"{attempt.status_code}")

    print("\n   ...and someone with no role at all may still raise one, as before")
    from app.database.postgres import SessionLocal as _Session
    from app.models.user import User
    from app.services.password_service import PasswordService

    with _Session() as db:
        loner = User(first_name="Loner", last_name=TAG, email=f"pra.loner.{TAG}@mailinator.com",
                     password=PasswordService.hash_password(PASSWORD), employee_id=f"EMP-PRA-LONER-{TAG}",
                     is_active=True, is_super_admin=False, must_change_password=False)
        db.add(loner)
        db.commit()
        created_users.append(str(loner.id))
    loner = login(f"pra.loner.{TAG}@mailinator.com", PASSWORD)
    check("they are told they may raise one",
          api("get", "/purchase-requisitions/meta", loner).json()["data"]["me"]["can_raise"] is True)
    check("My Requisitions is in their sidebar", "My Requisitions" in titles(loner))
    attempt = api("post", "/purchase-requisitions", loner, json={})
    if attempt.status_code == 200:
        created_prs.append(attempt.json()["data"]["id"])
    check("and they save a draft", attempt.status_code == 200, f"{attempt.status_code} {attempt.text[:160]}")

    check("a super admin is never stopped",
          api("get", "/purchase-requisitions/meta", admin).json()["data"]["me"]["can_raise"] is True)

except Exception as exc:  # noqa: BLE001 - reported, then cleaned up
    check("the run completes", False, repr(exc)[:200])

finally:
    print("\nClearing up")
    try:
        admin = login(*ADMIN)
        if original_bands:
            r = api("put", "/procurement/approval-matrix", admin, json={"bands": original_bands})
            print("   matrix put back:", r.status_code)
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        with SessionLocal() as db:
            prs = created_prs or [-1]
            db.execute(text("delete from procurement_activity where requisition_id = any(:p)"), {"p": prs})
            db.execute(text("delete from purchase_requisition where id = any(:p)"), {"p": prs})
            ids = created_users or ["00000000-0000-0000-0000-000000000000"]
            for sql in (
                "update users set reports_to_id = null where reports_to_id = any(cast(:ids as uuid[]))",
                "delete from user_roles where user_id = any(cast(:ids as uuid[]))",
                "delete from user_companies where user_id = any(cast(:ids as uuid[]))",
                "delete from users where id = any(cast(:ids as uuid[]))",
            ):
                db.execute(text(sql), {"ids": ids})
            db.commit()
        if created_role:
            r = api("delete", f"/rbac/roles/{created_role}", admin)
            print("   role removed:", r.status_code, "" if r.status_code < 400 else r.text[:120])
        print(f"   removed {len(created_prs)} draft requisitions and {len(created_users)} users")
    except Exception as exc:  # noqa: BLE001
        print("   cleanup problem:", exc)
        failed.append("cleanup")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
