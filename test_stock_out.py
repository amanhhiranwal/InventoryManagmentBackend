"""The Stock Out page: every unit issued from inventory, as it was recorded.

  * a super admin sees it, in the sidebar under SCM and from the API;
  * nobody else does until Roles & Access gives their role stock_out.read -
    the API refuses them and the sidebar leaves it out;
  * given the permission, a role sees it at once, and loses it again when
    it is taken away;
  * the records are the stored movements: serial, order, customer, where it
    went, who issued it and when, marked out or returned;
  * a tab that does not exist is refused, and the search narrows the list.

Run seed_sales_team.py and seed_fulfilment_roles.py first. It borrows the
Logistics role for the grant and takes the permission away again after.

    docker exec -w /app backend_app python test_stock_out.py
"""

import sys

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login, rows

passed, failed = [], []
section = ""


def check(name, condition, detail=""):
    (passed if condition else failed).append(f"{section}: {name}")
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + name
        + (f"   [{detail}]" if detail and not condition else "")
    )


def banner(title):
    global section
    section = title
    print(f"\n{title}")


def sidebar_paths(token):
    def walk(items):
        for item in items:
            if item.get("path"):
                yield item["path"]
            yield from walk(item.get("children") or [])

    return set(walk(rows(api("get", "/menus/sidebar", token))))


role = permission = None
admin = None

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    logistics = login("logistics@mailinator.com", TEAM_PASSWORD)

    # ================================================== the super admin
    banner("1. A super admin sees it")

    check("it is in the sidebar under SCM", "/inventory/stock-out" in sidebar_paths(admin))

    seen = api("get", "/inventory/stock-out", admin)
    check("the API answers", seen.status_code == 200, f"{seen.status_code} {seen.text[:120]}")

    body = seen.json() if seen.status_code == 200 else {}
    data = body.get("data") or []

    check(
        "with the figures across the top",
        set(body.get("summary") or {}) == {"units_out", "orders", "last_30_days", "returned"},
        str(body.get("summary")),
    )
    check(
        "and a count for each tab",
        set(body.get("counts") or {}) == {"all", "out", "returned"}
        and body["counts"]["all"] == body["counts"]["out"] + body["counts"]["returned"],
        str(body.get("counts")),
    )

    if data:
        row = data[0]
        check(
            "each row is the stored record: serial, order, customer, place, who and when",
            all(key in row for key in (
                "serial_number", "product", "model_number", "order_number",
                "customer_name", "place", "actor", "created_at", "returned",
            )),
            str(sorted(row)),
        )
        check(
            "newest first",
            [r["created_at"] for r in data] == sorted((r["created_at"] for r in data), reverse=True),
        )
        check("its time carries its offset", "+" in str(row["created_at"]) or "Z" in str(row["created_at"]), row["created_at"])

        serial = row["serial_number"]
        found = api("get", "/inventory/stock-out", admin, params={"search": serial}).json()
        check(
            "searching a serial finds its unit",
            any(r["serial_number"] == serial for r in found["data"]) and found["total"] <= body["total"],
            str(found.get("total")),
        )

        nothing = api("get", "/inventory/stock-out", admin, params={"search": "NO-SUCH-SERIAL-XYZ"}).json()
        check("a search that matches nothing returns nothing", nothing["total"] == 0, str(nothing.get("total")))

        out = api("get", "/inventory/stock-out", admin, params={"view": "out"}).json()
        check("the Out tab holds only units still out", all(not r["returned"] for r in out["data"]))
    else:
        print("  (nothing issued yet - the row checks are skipped)")

    bad = api("get", "/inventory/stock-out", admin, params={"view": "everything"})
    check("a tab that does not exist is refused", bad.status_code == 400, f"{bad.status_code}")

    paged = api("get", "/inventory/stock-out", admin, params={"page": 1, "size": 1}).json()
    check("it is paged", len(paged["data"]) <= 1, str(len(paged["data"])))

    # ================================================== returned, twice
    banner("2. A unit returned twice shows each return at its own time")

    import uuid
    from datetime import datetime, timedelta

    from app.database.mongodb import sync_mongo_db
    from app.database.postgres import SessionLocal
    from app.services.stock_movement_service import stock_out

    tag = uuid.uuid4().hex[:6].upper()
    serial = f"TWICE-{tag}"
    start = datetime.utcnow() - timedelta(hours=4)
    fake = {"order_id": -1, "order_number": None, "item_id": f"fake-{tag}", "serial_number": serial,
            "product": "Test Panel", "quantity": 1, "by_serial": True, "company_id": None}
    movements = sync_mongo_db["inventory_movements"]
    movements.insert_many([
        {**fake, "direction": "OUT", "created_at": start},
        {**fake, "direction": "IN", "created_at": start + timedelta(hours=1)},
        {**fake, "direction": "OUT", "created_at": start + timedelta(hours=2)},
        {**fake, "direction": "IN", "created_at": start + timedelta(hours=3)},
    ])

    try:
        session = SessionLocal()
        seen = stock_out({"is_super_admin": True}, session, search=serial)
        session.close()

        rows_seen = sorted(seen["rows"], key=lambda r: r["created_at"])
        check("both issues are listed, both returned", len(rows_seen) == 2 and all(r["returned"] for r in rows_seen), str(rows_seen))

        if len(rows_seen) == 2:
            check(
                "the first is returned at the first put-back, the second at the second",
                rows_seen[0]["returned_at"] < rows_seen[1]["created_at"] < rows_seen[1]["returned_at"],
                f"{rows_seen[0]['returned_at']} / {rows_seen[1]['returned_at']}",
            )

        before = stock_out({"is_super_admin": True}, SessionLocal())["summary"]["orders"]
        movements.update_many({"item_id": f"fake-{tag}", "direction": "IN"}, {"$set": {"direction": "VOID"}})
        after = stock_out({"is_super_admin": True}, SessionLocal())["summary"]["orders"]
        check("a unit out with no order number adds no order to the count", after == before, f"{before} -> {after}")
    finally:
        movements.delete_many({"item_id": f"fake-{tag}"})

    # ================================================== everybody else
    banner("3. Nobody else, until Roles & Access says so")

    refused = api("get", "/inventory/stock-out", logistics)
    check("the logistics desk is refused by the API", refused.status_code == 403, f"{refused.status_code}")
    check("and does not see it in the sidebar", "/inventory/stock-out" not in sidebar_paths(logistics))

    unsigned = api("get", "/inventory/stock-out", None)
    check("nor does anyone not signed in", unsigned.status_code in (401, 403), f"{unsigned.status_code}")

    permissions = {p["permission_name"]: p for p in rows(api("get", "/rbac/permissions", admin))}
    permission = permissions.get("stock_out.read")
    check("the permission is there to tick in Roles & Access", permission is not None)

    role = next((r for r in rows(api("get", "/rbac/roles", admin)) if r["role_name"] == "Logistics"), None)

    if permission and role:
        given = api("post", f"/rbac/roles/{role['id']}/permissions/{permission['id']}", admin)
        check("a super admin ticks it for Logistics", given.status_code < 400, f"{given.status_code} {given.text[:100]}")

        check("logistics can now read it", api("get", "/inventory/stock-out", logistics).status_code == 200)

        theirs = api("get", "/inventory/stock-out", logistics, params={"size": 100}).json()
        everyone = api("get", "/inventory/stock-out", admin, params={"size": 100}).json()
        check(
            "and sees the same units as the super admin, deleted products included",
            {r["id"] for r in theirs["data"]} == {r["id"] for r in everyone["data"]},
            f"{theirs.get('total')} vs {everyone.get('total')}",
        )
        check("and see it under SCM", "/inventory/stock-out" in sidebar_paths(logistics))

        api("delete", f"/rbac/roles/{role['id']}/permissions/{permission['id']}", admin)
        role = None

        check("taken away, the API refuses them again", api("get", "/inventory/stock-out", logistics).status_code == 403)
        check("and it leaves their sidebar", "/inventory/stock-out" not in sidebar_paths(logistics))

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    # Never leave the borrowed role holding the permission.
    if role and permission and admin:
        api("delete", f"/rbac/roles/{role['id']}/permissions/{permission['id']}", admin)
        print("\n   took stock_out.read back off Logistics")

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
