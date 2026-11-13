"""The accounts desk's history: what accounts already approved or rejected.

/fulfilment/accounts/history lists the decisions accounts took on sales
orders - advances verified and balances settled (APPROVED), orders put on
hold (REJECTED) - read from the activity each decision writes:

  * only the accounts desk (payment_desk.read) may read it;
  * APPROVED is every move out of an accounts stage to the next stage;
  * REJECTED is every move out of an accounts stage onto hold, with the
    reason, and a new one shows at the top;
  * anything else is refused.

One activity row is added for the rejected case and removed at the end.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_accounts_history.py
"""

import sys

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


from sqlalchemy import text  # noqa: E402

from app.core.fulfilment import ACCOUNTS, DESK_STAGES, DESKS  # noqa: E402
from app.database.postgres import SessionLocal  # noqa: E402

admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
outsider = login("zh.north@mailinator.com", TEAM_PASSWORD)
stages = DESK_STAGES[ACCOUNTS]
onward = [DESKS[s].approves_to for s in stages]

db = SessionLocal()
planted = None

try:
    print("\nWho may read it")
    check("someone outside accounts cannot", api("get", "/fulfilment/accounts/history", outsider).status_code == 403)
    check("an unknown decision is refused",
          api("get", "/fulfilment/accounts/history", admin, params={"decision": "MAYBE"}).status_code == 400)

    print("\nApproved")
    r = api("get", "/fulfilment/accounts/history", admin, params={"decision": "APPROVED", "size": 200})
    body = r.json().get("data") or {}
    expected = db.execute(text(
        "SELECT count(*) FROM sales_order_activity WHERE from_status = ANY(:f) AND to_status = ANY(:t)"),
        {"f": stages, "t": onward}).scalar()
    check("the accounts desk can read it", r.status_code == 200, r.text[:150])
    check("it holds every accounts approval on record", body.get("total") == expected, f"{body.get('total')} vs {expected}")
    rows = body.get("rows") or []
    check("each with the order, who, when, what for and the amount",
          all({"order_number", "decided_by_name", "decided_at", "kind", "amount"} <= set(row) for row in rows), str(rows[:1]))
    check("newest first", [row["decided_at"] for row in rows] == sorted((row["decided_at"] for row in rows), reverse=True))

    print("\nRejected")
    order_id = db.execute(text("SELECT id FROM sales_order ORDER BY id DESC LIMIT 1")).scalar()
    if order_id is None:
        print("  (no sales order to plant a rejection on - skipped)")
    else:
        planted = db.execute(text(
            "INSERT INTO sales_order_activity (sales_order_id, action, description, from_status, to_status, created_at) "
            "VALUES (:o, 'Rejected By Accounts', 'Test: advance not in the bank yet', :f, 'ON_HOLD', now()) RETURNING id"),
            {"o": order_id, "f": stages[0]}).scalar()
        db.commit()
        r = api("get", "/fulfilment/accounts/history", admin, params={"decision": "REJECTED"})
        rows = (r.json().get("data") or {}).get("rows") or []
        top = rows[0] if rows else {}
        check("a rejection shows at the top", top.get("id") == planted, str(top))
        check("with the reason", top.get("remarks") == "Test: advance not in the bank yet", str(top.get("remarks")))
        check("and is not counted as approved",
              planted not in [row["id"] for row in (api("get", "/fulfilment/accounts/history", admin,
                                                         params={"decision": "APPROVED", "size": 200}).json()["data"]["rows"])])

    print("\nProduction and logistics keep the same history")
    from app.core.fulfilment import LOGISTICS, PRODUCTION

    for desk, role in (("production", PRODUCTION), ("logistics", LOGISTICS)):
        check(f"someone outside {desk} cannot read its history",
              api("get", f"/fulfilment/{desk}/history", outsider).status_code == 403)
        desk_stages = DESK_STAGES[role]
        for decision, targets in (("APPROVED", [DESKS[s].approves_to for s in desk_stages]), ("REJECTED", ["ON_HOLD"])):
            r = api("get", f"/fulfilment/{desk}/history", admin, params={"decision": decision, "size": 200})
            got = (r.json().get("data") or {}) if r.status_code == 200 else {}
            want = db.execute(text(
                "SELECT count(*) FROM sales_order_activity WHERE from_status = ANY(:f) AND to_status = ANY(:t)"),
                {"f": desk_stages, "t": targets}).scalar()
            check(f"{desk} {decision.lower()}: every decision on record", got.get("total") == want,
                  f"{got.get('total')} vs {want} ({r.status_code})")
            check(f"{desk} {decision.lower()}: shows the order value, not an advance",
                  all(row["kind"] == "STAGE" and row["desk"] == role for row in got.get("rows") or []))

finally:
    if planted:
        db.execute(text("DELETE FROM sales_order_activity WHERE id = :i"), {"i": planted})
        db.commit()
    db.close()

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
