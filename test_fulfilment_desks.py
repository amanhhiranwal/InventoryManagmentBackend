"""The accounts and procurement desks, and who may do what at them.

An approved order stops being the salesperson's to push. It goes to
accounts, who confirm the money; then to inventory, who confirm the stock
and send it out; and everyone above them watches the tracking board. This
checks all of that, including the parts that should be refused:

  * a desk sees only its own queue, and is refused the other one;
  * a salesperson is refused every stage the desks own;
  * an approval moves the order exactly one step, a rejection holds it;
  * a rejection with no reason is refused;
  * the queue's numbers agree with the queue;
  * the stock figures come from what is actually on the shelf;
  * a super admin's notifications cover everybody, and nobody else's do.

Run seed_sales_team.py and seed_fulfilment_roles.py first. Everything it
creates is removed again.

    docker exec -w /app backend_app python test_fulfilment_desks.py
"""

import sys
import uuid
from datetime import datetime, timezone

from seed_sales_team import TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
ACCOUNTS = "accounts@mailinator.com"
INVENTORY = "inventory@mailinator.com"

TAG = uuid.uuid4().hex[:6]

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


token = {}
created_orders = []

try:
    admin = login(*ADMIN)

    for key in ("ceo", "avp", "am_north_1"):
        token[key] = login(email_for(key), TEAM_PASSWORD)

    token["accounts"] = login(ACCOUNTS, TEAM_PASSWORD)
    token["inventory"] = login(INVENTORY, TEAM_PASSWORD)

    print("Signed in as the super admin, the sales team and both desks.")

    owner = token["am_north_1"]
    now = datetime.now(timezone.utc)

    def raise_order(label, sku="NX-9K-QIFP75-EX", qty=2):
        created = api("post", "/orders", owner, json={
            "customer_name": f"{label} {TAG}",
            "company_name": "Synergy North Agro",
            "state": "Delhi",
            "order_date": now.isoformat(),
            "items": [{
                "product": "Interactive Flat Panel 75in",
                "sku": sku,
                "qty": qty,
                "rate": 185000,
                "tax_rate": 18,
            }],
        })

        order = created.json()["data"]
        created_orders.append(order["id"])

        api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})

        return order

    def status_of(order_id):
        return api("get", f"/orders/{order_id}", admin).json()["data"]["status"]

    # ============================================= who may open which desk
    banner("1. Each desk is its own")

    check(
        "accounts open the accounts desk",
        api("get", "/fulfilment/accounts", token["accounts"]).status_code == 200,
    )
    check(
        "inventory open the procurement desk",
        api("get", "/fulfilment/procurement", token["inventory"]).status_code == 200,
    )
    check(
        "accounts are refused the procurement desk",
        api("get", "/fulfilment/procurement", token["accounts"]).status_code == 403,
    )
    check(
        "inventory are refused the accounts desk",
        api("get", "/fulfilment/accounts", token["inventory"]).status_code == 403,
    )
    check(
        "an Area Manager is refused both",
        api("get", "/fulfilment/accounts", token["am_north_1"]).status_code == 403
        and api("get", "/fulfilment/procurement", token["am_north_1"]).status_code == 403,
    )
    check(
        "the CEO is refused them too - it is not their job",
        api("get", "/fulfilment/accounts", token["ceo"]).status_code == 403,
    )
    check(
        "a super admin stands in at either desk",
        api("get", "/fulfilment/accounts", admin).status_code == 200
        and api("get", "/fulfilment/procurement", admin).status_code == 200,
    )

    # ================================================ the accounts queue
    banner("2. The accounts queue")

    order = raise_order("Desk Test")

    queue = api("get", "/fulfilment/accounts", token["accounts"]).json()["data"]
    mine = [row for row in queue["orders"] if row["id"] == order["id"]]

    check("a confirmed order lands on the accounts desk", len(mine) == 1)

    if mine:
        row = mine[0]
        check("it says what accounts are being asked", bool(row["asks"]), str(row["asks"]))
        check(
            "and where approving sends it",
            row["approves_to"] == "PAYMENT_VERIFIED",
            str(row["approves_to"]),
        )
        check(
            "the advance is shown as not yet settled",
            row["advance_settled"] is False,
            str(row["advance_received"]),
        )

    kpis = {k["key"]: k["value"] for k in queue["kpis"]}
    waiting = [r for r in queue["orders"] if r["status"] == "CONFIRMED"]

    check(
        "the Awaiting Verification figure matches the queue",
        kpis["awaiting_verification"] == len(waiting),
        f"{kpis['awaiting_verification']} vs {len(waiting)}",
    )
    check(
        "the Value Pending figure matches the queue",
        abs(kpis["value_pending"] - sum(r["grand_total"] for r in waiting)) < 1,
        str(kpis["value_pending"]),
    )

    # ============================================== who may move it along
    banner("3. Only the desk holding it may move it")

    blocked = api("put", f"/orders/{order['id']}/status", owner, json={
        "status": "PAYMENT_VERIFIED",
    })
    check("the salesperson cannot verify their own payment", blocked.status_code == 403, f"got {blocked.status_code}")
    check(
        "and is told whose desk it is",
        "Accounts" in blocked.text,
        blocked.text[:120],
    )

    blocked = api("put", f"/fulfilment/orders/{order['id']}/decide", token["inventory"], json={
        "approve": True,
    })
    check("inventory cannot decide an accounts step", blocked.status_code == 403, f"got {blocked.status_code}")

    empty = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": False, "remarks": "  ",
    })
    check("a rejection with no reason is refused", empty.status_code == 400, f"got {empty.status_code}")

    # A salesperson may still put their own order on hold or cancel it -
    # that is a business call, not a desk's confirmation.
    held = api("put", f"/orders/{order['id']}/status", owner, json={
        "status": "ON_HOLD", "remarks": "Client asked us to pause.",
    })
    check("the salesperson can still hold their own order", held.status_code == 200, f"{held.status_code} {held.text[:120]}")

    api("put", f"/orders/{order['id']}/status", admin, json={"status": "CONFIRMED"})

    # ================================================== the decision
    banner("4. Accounts decide")

    approved = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Advance received in full.",
    })
    check("accounts approve it", approved.status_code == 200, f"{approved.status_code} {approved.text[:160]}")
    check("it moves exactly one step", status_of(order["id"]) == "PAYMENT_VERIFIED", status_of(order["id"]))

    history = rows(api("get", f"/orders/{order['id']}/activities", admin))
    check(
        "the reason is on the record",
        any("Advance received in full." == (e.get("description") or "") for e in history),
        str([e.get("description") for e in history][:3]),
    )

    check(
        "it has left the accounts queue",
        order["id"] not in {
            r["id"]
            for r in api("get", "/fulfilment/accounts", token["accounts"]).json()["data"]["orders"]
        },
    )

    # ============================================== the procurement queue
    banner("5. Inventory pick it up")

    desk = api("get", "/fulfilment/procurement", token["inventory"]).json()["data"]
    mine = [row for row in desk["orders"] if row["id"] == order["id"]]

    check("it arrives on the procurement desk", len(mine) == 1)

    if mine:
        stock = mine[0]["stock"]
        check("the order's lines are checked against stock", len(stock) == 1, str(stock))

        if stock:
            line = stock[0]
            # Read the catalogue rather than hard-coding a figure: stock
            # moves now, so yesterday's number is not today's.
            on_hand = next(
                (
                    float((i.get("attributes") or {}).get("instock") or 0)
                    for i in rows(api("get", "/inventory/items", token["inventory"]))
                    if str(i.get("serial_number") or "").upper() == "NX-9K-QIFP75-EX"
                ),
                None,
            )

            check("the line is matched to a real item", line["known"] is True, str(line))
            check(
                "it reports what is actually on the shelf",
                line["available"] == on_hand,
                f"desk says {line['available']}, catalogue says {on_hand}",
            )
            check(
                "an order it can cover is not flagged short",
                line["short"] is (line["wanted"] > (on_hand or 0)),
                str(line),
            )

    # An order for something the shelf cannot cover is flagged.
    short_order = raise_order("Short Order", sku="NX-OPS-I5-8-256", qty=4)
    api("put", f"/fulfilment/orders/{short_order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Paid.",
    })

    desk = api("get", "/fulfilment/procurement", token["inventory"]).json()["data"]
    short_row = next((r for r in desk["orders"] if r["id"] == short_order["id"]), None)

    check("an order the shelf cannot cover is flagged", short_row and short_row["stock_short"] is True, str(short_row and short_row["stock"]))

    kpis = {k["key"]: k["value"] for k in desk["kpis"]}
    check("and counted as short", kpis["short"] >= 1, str(kpis["short"]))

    # ============================================ walking it to the end
    banner("6. Out the door")

    for expected in ("PROCUREMENT", "READY", "DISPATCHED", "DELIVERED"):
        step = api("put", f"/fulfilment/orders/{order['id']}/decide", token["inventory"], json={
            "approve": True, "remarks": f"Moving to {expected.lower()}.",
        })
        check(
            f"inventory move it to {expected.lower()}",
            step.status_code == 200 and status_of(order["id"]) == expected,
            f"{step.status_code} {status_of(order['id'])}",
        )

    # Delivered to installed is the salesperson's: they are the ones on site.
    installed = api("put", f"/orders/{order['id']}/status", owner, json={"status": "INSTALLED"})
    check("the salesperson signs off the installation", installed.status_code == 200, f"{installed.status_code} {installed.text[:120]}")

    back = api("get", "/fulfilment/accounts", token["accounts"]).json()["data"]
    check(
        "an installed order comes back to accounts for the balance",
        order["id"] in {r["id"] for r in back["orders"]},
    )

    closed = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Balance settled.",
    })
    check("accounts close it", closed.status_code == 200 and status_of(order["id"]) == "COMPLETED", status_of(order["id"]))

    # ==================================================== a rejection
    banner("7. A rejection holds the order")

    rejected_order = raise_order("Rejected Order")

    rejected = api("put", f"/fulfilment/orders/{rejected_order['id']}/decide", token["accounts"], json={
        "approve": False, "remarks": "Nothing has come into the bank.",
    })
    check("accounts reject it", rejected.status_code == 200, f"{rejected.status_code} {rejected.text[:160]}")
    check("it goes on hold", status_of(rejected_order["id"]) == "ON_HOLD", status_of(rejected_order["id"]))

    history = rows(api("get", f"/orders/{rejected_order['id']}/activities", admin))
    check(
        "the rejection names the desk",
        any("Rejected By Accounts" == (e.get("action") or "") for e in history),
        str([e.get("action") for e in history][:3]),
    )

    # ================================================== notifications
    banner("8. Everyone sees their own work")

    def bell(who):
        return api("get", "/notifications", token[who] if who in token else who).json()

    accounts_bell = bell("accounts")
    check(
        "the accounts desk was told about its own queue",
        any("Accounts" in (n.get("action") or "") or "Verify" in (n.get("action") or "")
            for n in accounts_bell["data"]),
        str([n.get("action") for n in accounts_bell["data"]][:4]),
    )
    check(
        "and every row in their bell is theirs",
        all(not n.get("for_user") for n in accounts_bell["data"]),
    )

    avp_bell = bell("avp") if "avp" in token else bell(login(email_for("avp"), TEAM_PASSWORD))
    check(
        "the AVP's bell is their own work only",
        all(not n.get("for_user") for n in avp_bell["data"]),
    )

    admin_bell = api("get", "/notifications", admin).json()
    check(
        "a super admin's bell covers the whole business",
        any(n.get("for_user") for n in admin_bell["data"]),
        str([n.get("for_user") for n in admin_bell["data"]][:4]),
    )
    check(
        "and names whose each one is",
        all(
            n.get("for_user") for n in admin_bell["data"]
        ),
        str([n.get("for_user") for n in admin_bell["data"]][:4]),
    )

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    banner("9. Clearing what the test created")

    try:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()

        def run(sql, **params):
            try:
                session.execute(text(sql), params)
            except Exception as exc:
                session.rollback()
                print("   could not clear:", str(exc).split("\n")[0][:90])

        if created_orders:
            # Every stage rings the bell, so those rows go too - otherwise a
            # test run leaves stale orders in real people's notifications.
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=created_orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)

        session.commit()

        left = session.execute(
            text("select count(*) from sales_order where id = any(:ids)"),
            {"ids": created_orders or [0]},
        ).scalar()

        print(f"   removed {len(created_orders)} orders")
        print(f"   left behind: {left}")

        session.close()
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
