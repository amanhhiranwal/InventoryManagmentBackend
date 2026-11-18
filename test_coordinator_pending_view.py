"""The business coordinator reads sales orders waiting for approval.

With the Roles & Access tick order.pending_approval.read (given to the
Business Coordinator role on release):
  * the Pending Approval tab lists every pending order in the business,
    the same ones the super admin sees, each naming the approver it is with;
  * a pending order opens, and its approval says who it is waiting on;
  * reading only: editing it, moving its status, logging an activity on it,
    deleting it, approving it and withdrawing its request are all refused;
  * drafts and cancelled orders stay hidden, as before.

Without the tick (taken off the role for the test, then put back):
  * pending orders are hidden again, in the list and when opened by id.

A salesperson's list is unchanged apart from the new waiting_on field.

Changes nothing it does not put back.

    docker exec -w /app backend_app python test_coordinator_pending_view.py
"""

import sys

from app.models.approval import ApprovalDocument
from app.services.sales_order_service import SEE_PENDING_APPROVALS
from seed_sales_team import TEAM_PASSWORD, api, email_for, login

ADMIN = ("superadmin@mailinator.com", "password123")
COORDINATOR = ("coordinator@mailinator.com", TEAM_PASSWORD)

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def orders(token, status):
    r = api("get", "/orders", token, params={"status": status, "page": 1, "size": 500})
    assert r.status_code == 200, r.text[:200]
    return r.json()["data"]


def ids(rows):
    return {str(row.get("_id") or row.get("id")) for row in rows}


def set_tick(on: bool) -> None:
    """Give or take the permission from the Business Coordinator role."""

    from app.database.postgres import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.role_name == "Business Coordinator").one()
        permission = db.query(Permission).filter(Permission.permission_name == SEE_PENDING_APPROVALS).one()
        held = db.query(RolePermission).filter(
            RolePermission.role_id == role.id, RolePermission.permission_id == permission.id
        )
        if on and not held.first():
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        if not on:
            held.delete()
        db.commit()
    finally:
        db.close()


admin = login(*ADMIN)
set_tick(True)
coordinator = login(*COORDINATOR)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)

try:
    print("\nWith the tick")
    pending_admin = orders(admin, "PENDING_APPROVAL")
    pending_seen = orders(coordinator, "PENDING_APPROVAL")
    check("there are pending orders to look at", len(pending_admin) > 0, str(len(pending_admin)))
    check("the coordinator sees every pending order the super admin does",
          ids(pending_seen) == ids(pending_admin), f"{len(pending_seen)} vs {len(pending_admin)}")
    named = [row for row in pending_seen if row.get("waiting_on")]
    check("each names the approver it is with", len(named) == len(pending_seen),
          f"{len(named)} of {len(pending_seen)} named")

    target = pending_seen[0] if pending_seen else None
    if target:
        order_id = target.get("_id") or target.get("id")
        r = api("get", f"/orders/{order_id}", coordinator)
        check("a pending order opens", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        r = api("get", f"/approvals/document/{ApprovalDocument.SALES_ORDER}/{order_id}", coordinator)
        approval = (r.json() or {}).get("data") or {}
        check("and its approval says who it is waiting on",
              approval.get("waiting_on") == target["waiting_on"], f"{approval.get('waiting_on')} vs {target['waiting_on']}")

        print("\nReading only")
        r = api("put", f"/orders/{order_id}", coordinator, json={"customer_name": "Changed"})
        check("editing it is refused", r.status_code in (401, 403), f"{r.status_code} {r.text[:120]}")
        r = api("put", f"/orders/{order_id}/status", coordinator, json={"status": "DRAFT"})
        check("moving its status is refused", r.status_code in (400, 403), f"{r.status_code} {r.text[:120]}")
        r = api("post", f"/orders/{order_id}/activities", coordinator, json={"remarks": "test", "status": "CANCELLED"})
        check("logging an activity on it is refused", r.status_code in (400, 403), f"{r.status_code} {r.text[:120]}")
        r = api("delete", f"/orders/{order_id}", coordinator)
        check("deleting it is refused", r.status_code in (401, 403), f"{r.status_code} {r.text[:120]}")
        if approval.get("id"):
            r = api("put", f"/approvals/{approval['id']}/decide", coordinator, json={"approve": True, "remarks": "test"})
            check("approving it is refused", r.status_code in (400, 403), f"{r.status_code} {r.text[:120]}")
            r = api("put", f"/approvals/{approval['id']}/withdraw", coordinator)
            check("withdrawing its approval request is refused", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
        after = api("get", f"/orders/{order_id}", admin).json()["data"]
        check("and the order is still waiting for approval", after.get("status") == "PENDING_APPROVAL", str(after.get("status")))

    print("\nStill hidden")
    check("drafts", orders(coordinator, "DRAFT") == [])
    check("cancelled orders", orders(coordinator, "CANCELLED") == [])
    drafts = orders(admin, "DRAFT")
    if drafts:
        r = api("get", f"/orders/{drafts[0].get('_id') or drafts[0].get('id')}", coordinator)
        check("a draft does not open", r.status_code == 403, str(r.status_code))

    print("\nA salesperson")
    mine = orders(salesperson, "PENDING_APPROVAL")
    check("sees only their own line's pending orders, as before",
          ids(mine) <= ids(pending_admin) and len(mine) <= len(pending_admin))

    print("\nWithout the tick")
    set_tick(False)
    coordinator = login(*COORDINATOR)
    check("pending orders are hidden again", orders(coordinator, "PENDING_APPROVAL") == [])
    if target:
        r = api("get", f"/orders/{order_id}", coordinator)
        check("and do not open by id", r.status_code == 403, str(r.status_code))
    approved = orders(coordinator, "CONFIRMED")
    check("approved orders are still listed", len(approved) == len(orders(admin, "CONFIRMED")),
          f"{len(approved)}")
finally:
    set_tick(True)
    print("\n   the tick is back on the Business Coordinator role")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
