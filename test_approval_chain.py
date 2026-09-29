"""The discount approval chain, run by the seeded Sales team.

Run seed_sales_team.py first. An Area Manager raises quotations at three
discount levels and checks each goes to the right people:

    10%  ->  their own AVP, and nobody else's
    18%  ->  the AVP, then the CEO
    25%  ->  the AVP, the CEO, then the founder

It also checks the dealer price goes straight to the CEO, that an
undiscounted quotation needs no approval at all, that the document is held
at Pending Approval while the chain runs and released when it clears, and
that a rejection hands it back.

Everything it creates is removed again.

    docker exec -w /app backend_app python test_approval_chain.py
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone

from seed_sales_team import TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

passed, failed = [], []
section = ""


def check(name, condition, detail=""):
    (passed if condition else failed).append(f"{section}: {name}")
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def banner(title):
    global section
    section = title
    print(f"\n{title}")


token = {}
created_quotations = []
created_approvals = []
created_orders = []

try:
    admin = login(*ADMIN)
    everyone = ["ceo", "avp", "zh_north", "zh_south", "am_north_1", "am_south_1"]

    for key in everyone:
        token[key] = login(email_for(key), TEAM_PASSWORD)

    # The desks that own an order once it has been approved.
    token["accounts"] = login("accounts@mailinator.com", TEAM_PASSWORD)
    token["inventory"] = login("inventory@mailinator.com", TEAM_PASSWORD)
    print("Signed in as the super admin and the team.")

    # ------------------------------------------------------------ matrix
    banner("1. The discount matrix")

    matrix = api("get", "/approvals/matrix", admin).json()["data"]
    bands = {b["role"]: b for b in matrix["bands"]}

    check("the AVP carries the first 15%", bands["AVP"]["to_percent"] == 15.0, str(bands.get("AVP")))
    check("the CEO carries up to 20%", bands["CEO"]["to_percent"] == 20.0, str(bands.get("CEO")))
    check("past that it is the founder", bands["Founder"]["to_percent"] is None, str(bands.get("Founder")))

    def preview(discount, price_type="ECP"):
        return api(
            "get", "/approvals/preview", token["am_north_1"],
            params={"price_type": price_type, "discount_percent": discount},
        ).json()["data"]

    check("no discount needs no approval", preview(0)["chain"] == [], str(preview(0)))
    check("10% stops at the AVP", preview(10)["chain"] == ["AVP"], str(preview(10)["chain"]))
    check("15% still stops at the AVP", preview(15)["chain"] == ["AVP"], str(preview(15)["chain"]))
    check("18% reaches the CEO", preview(18)["chain"] == ["AVP", "CEO"], str(preview(18)["chain"]))
    check("25% reaches the founder", preview(25)["chain"] == ["AVP", "CEO", "Founder"], str(preview(25)["chain"]))
    check("dealer price goes to the CEO alone", preview(0, "DP")["chain"] == ["CEO"], str(preview(0, "DP")["chain"]))

    # ------------------------------------------------------- a quotation
    banner("2. An Area Manager raises a discounted quotation")

    def make_quotation(label):
        r = api("post", "/quotations/", token["am_north_1"], json={
            "organization_name": f"{label} {TAG}",
            "contact_name": "Approval Test",
            "email": f"approval.{TAG}@mailinator.com",
            "mobile_number": "+91 9812345670",
            "quotation_date": datetime.now(timezone.utc).isoformat(),
            "validation_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "items": [{"product": "Panel", "model": "IFP-75", "sku": f"SKU-{TAG}",
                       "quantity": 10, "unit_price": 100000, "tax": 18}],
        })
        if r.status_code >= 400:
            raise RuntimeError(f"{label}: {r.status_code} {r.text[:200]}")
        q = r.json()["data"]
        created_quotations.append(q["id"])
        return q

    quotation = make_quotation("Banded Discount")

    # The quote itself never carries the discount: 10 x 100,000 stands.
    check(
        "the quote is the list price, with no discount applied",
        float(quotation["subtotal"]) == 1000000 and float(quotation["taxable_amount"]) == 1000000,
        f"subtotal {quotation['subtotal']}, taxable {quotation['taxable_amount']}",
    )

    def request(quotation_id, discount, price_type="ECP", who="am_north_1"):
        return api("post", "/approvals", token[who], json={
            "document_type": "QUOTATION",
            "document_id": quotation_id,
            "document_number": f"QT-TEST-{TAG}",
            "price_type": price_type,
            "discount_percent": discount,
            "discount_amount": 1000000 * discount / 100,
            "document_value": 1000000,
            "remarks": "Raised by the approval chain test.",
        })

    r = request(quotation["id"], 18)
    check("18% is sent up for approval", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    approval = r.json()["data"]
    if approval:
        created_approvals.append(approval["id"])

    check("it is waiting on the AVP first", approval and approval["waiting_on"] == "AVP", str(approval and approval["waiting_on"]))
    check("both steps are recorded up front", approval and [s["role"] for s in approval["steps"]] == ["AVP", "CEO"], str(approval and [s["role"] for s in approval["steps"]]))

    held = api("get", f"/quotations/{quotation['id']}", token["am_north_1"]).json()["data"]
    check("the quotation is held at Pending Approval", held["status"] == "PENDING_APPROVAL", held["status"])

    # ------------------------------------------------------ who may act
    banner("3. Only the right person can approve")

    def pending_ids(who):
        return {a["id"] for a in rows(api("get", "/approvals/pending", token[who]))}

    check("it is in the AVP's queue", approval["id"] in pending_ids("avp"))
    check("it is not in the CEO's queue yet", approval["id"] not in pending_ids("ceo"))
    check("nor the Zonal Head's - they have no discounting power", approval["id"] not in pending_ids("zh_north"))
    check("nor the requester's own", approval["id"] not in pending_ids("am_north_1"))

    r = api("put", f"/approvals/{approval['id']}/decide", token["zh_north"], json={"approve": True})
    check("a Zonal Head cannot approve it", r.status_code == 403, f"got {r.status_code}")

    r = api("put", f"/approvals/{approval['id']}/decide", token["ceo"], json={"approve": True})
    check(
        "the CEO cannot jump the AVP's step",
        r.status_code == 403,
        f"got {r.status_code} {r.text[:120]}",
    )

    # ------------------------------------------------------ up the chain
    banner("4. Up the chain, one step at a time")

    r = api("put", f"/approvals/{approval['id']}/decide", token["avp"], json={
        "approve": True, "remarks": "Within my authority up to 15%, passing the rest up.",
    })
    check("the AVP approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    after_avp = r.json()["data"] if r.status_code == 200 else {}
    check("it now waits on the CEO", after_avp.get("waiting_on") == "CEO", str(after_avp.get("waiting_on")))
    check("it is still pending overall", after_avp.get("status") == "PENDING", str(after_avp.get("status")))
    check("the AVP's decision is recorded against them", (after_avp.get("steps") or [{}])[0].get("approver_name"), str((after_avp.get("steps") or [{}])[0]))

    still_held = api("get", f"/quotations/{quotation['id']}", token["am_north_1"]).json()["data"]
    check("the quotation is still held", still_held["status"] == "PENDING_APPROVAL", still_held["status"])

    check("it has moved into the CEO's queue", approval["id"] in pending_ids("ceo"))

    r = api("put", f"/approvals/{approval['id']}/decide", token["ceo"], json={
        "approve": True, "remarks": "Approved.",
    })
    check("the CEO approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    final = r.json()["data"] if r.status_code == 200 else {}
    check("the request is fully approved", final.get("status") == "APPROVED", str(final.get("status")))
    check("nothing is waiting on anyone", final.get("waiting_on") is None, str(final.get("waiting_on")))

    released = api("get", f"/quotations/{quotation['id']}", token["am_north_1"]).json()["data"]
    check("the quotation is released to be sent", released["status"] == "DRAFT", released["status"])

    history = rows(api("get", f"/quotations/{quotation['id']}/activities", token["am_north_1"]))
    actions = [entry["action"] for entry in history]
    check("the history records both the request and the approval",
          "Sent For Approval" in actions and "Discount Approved" in actions, str(actions))

    # ------------------------------------------------------- a rejection
    banner("5. A rejection hands it back")

    rejected_quote = make_quotation("Rejected Discount")
    r = request(rejected_quote["id"], 10)
    check("10% goes up", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    second = r.json()["data"]
    if second:
        created_approvals.append(second["id"])

    r = api("put", f"/approvals/{second['id']}/decide", token["avp"], json={
        "approve": False, "remarks": "Too thin on this account.",
    })
    check("the AVP rejects it", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    check("the request reads as rejected", r.json()["data"]["status"] == "REJECTED", str(r.json()["data"]["status"]))

    back = api("get", f"/quotations/{rejected_quote['id']}", token["am_north_1"]).json()["data"]
    check("the quotation is back to draft", back["status"] == "DRAFT", back["status"])

    # -------------------------------------------------- no approval path
    banner("6. No discount, no approval")

    clean = make_quotation("No Discount")
    r = request(clean["id"], 0)
    check("an undiscounted quotation raises nothing", r.status_code == 200 and r.json()["data"] is None, f"{r.status_code} {r.text[:150]}")

    untouched = api("get", f"/quotations/{clean['id']}", token["am_north_1"]).json()["data"]
    check("and is left as a draft", untouched["status"] == "DRAFT", untouched["status"])

    # ------------------------------------------------------ dealer price
    banner("7. Dealer price is the CEO's alone")

    dealer_quote = make_quotation("Dealer Price")
    r = request(dealer_quote["id"], 0, price_type="DP")
    check("it is raised even with no discount", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    dealer = r.json()["data"]
    if dealer:
        created_approvals.append(dealer["id"])

    check("it waits on the CEO, not the AVP", dealer and dealer["waiting_on"] == "CEO", str(dealer and dealer["waiting_on"]))
    check("it is not in the AVP's queue", dealer and dealer["id"] not in pending_ids("avp"))

    r = api("put", f"/approvals/{dealer['id']}/decide", token["avp"], json={"approve": True})
    check("an AVP cannot set a transfer price", r.status_code == 403, f"got {r.status_code}")

    r = api("put", f"/approvals/{dealer['id']}/decide", token["ceo"], json={"approve": True})
    check("the CEO can", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    # ------------------------------------------------------- other zones
    banner("8. Another zone's deal is not yours to approve")

    other = api("post", "/quotations/", token["am_south_1"], json={
        "organization_name": f"South Deal {TAG}",
        "contact_name": "South Contact",
        "email": f"south.{TAG}@mailinator.com",
        "quotation_date": datetime.now(timezone.utc).isoformat(),
        "items": [{"product": "Panel", "model": "IFP-65", "quantity": 5,
                   "unit_price": 80000, "tax": 18}],
    })
    if other.status_code < 400:
        south_quote = other.json()["data"]
        created_quotations.append(south_quote["id"])

        r = api("post", "/approvals", token["am_south_1"], json={
            "document_type": "QUOTATION",
            "document_id": south_quote["id"],
            "document_number": f"QT-SOUTH-{TAG}",
            "price_type": "ECP",
            "discount_percent": 12,
            "document_value": 400000,
        })
        check("the South Area Manager raises one", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

        south_approval = r.json()["data"]
        if south_approval:
            created_approvals.append(south_approval["id"])

            # Both zones report to the same AVP here, so the AVP does see
            # it - what a Zonal Head must not see is the other zone's.
            check("it reaches the AVP both zones report to", south_approval["id"] in pending_ids("avp"))
            check(
                "the North Zonal Head has no part in it",
                south_approval["id"] not in pending_ids("zh_north"),
            )

    # --------------------------------------------- the fulfilment chain
    banner("9. A sales order walks the fulfilment chain")

    r = api("post", "/orders", token["am_north_1"], json={
        "customer_name": f"Fulfilment Test {TAG}",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [{"product": "Panel", "model": "IFP-75", "qty": 4,
                   "rate": 100000, "tax_rate": 18}],
    })
    check("an order is raised", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    if r.status_code == 200:
        order = r.json()["data"]
        created_orders.append(order["id"])

        # Who may push each step: the sales owner up to Confirmed, then the
        # desks, because once an order is approved it stops being theirs.
        chain = [
            ("PENDING_APPROVAL", "sent for approval", "am_north_1"),
            ("CONFIRMED", "approved", "am_north_1"),
            ("PAYMENT_VERIFIED", "payment verified by accounts", "accounts"),
            ("PROCUREMENT", "with inventory", "inventory"),
            ("READY", "ready to dispatch", "inventory"),
            ("DISPATCHED", "dispatched", "inventory"),
            ("DELIVERED", "delivered", "inventory"),
            ("INSTALLED", "installed", "am_north_1"),
            ("COMPLETED", "won", "accounts"),
        ]

        for status, label, who in chain:
            step = api("put", f"/orders/{order['id']}/status", token[who], json={
                "status": status, "remarks": f"Moved to {label}.",
            })
            check(f"the order reaches {label}", step.status_code == 200, f"{status}: {step.status_code} {step.text[:140]}")

            if step.status_code != 200:
                break

        final = api("get", f"/orders/{order['id']}", token["am_north_1"]).json()["data"]
        check("it finishes Completed", final["status"] == "COMPLETED", final["status"])

        history = [a["action"] for a in rows(api("get", f"/orders/{order['id']}/activities", token["am_north_1"]))]
        check(
            "every step is in the history",
            {"Payment Verified", "Order Dispatched", "Installation Completed"} <= set(history),
            str(history),
        )

        # Skipping a step is refused: procurement cannot be jumped.
        r = api("post", "/orders", token["am_north_1"], json={
            "customer_name": f"Skip Test {TAG}",
            "order_date": datetime.now(timezone.utc).isoformat(),
            "items": [{"product": "Panel", "qty": 1, "rate": 1000, "tax_rate": 18}],
        })
        if r.status_code == 200:
            skipper = r.json()["data"]
            created_orders.append(skipper["id"])

            r = api("put", f"/orders/{skipper['id']}/status", token["am_north_1"], json={
                "status": "DISPATCHED",
            })
            check("a draft cannot jump straight to dispatched", r.status_code == 400, f"got {r.status_code}")

finally:
    banner("10. Clearing what the test created")
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

        if created_approvals:
            run("delete from sales_approval where id = any(:ids)", ids=created_approvals)
        # The bell rings for every step, so those rows go too - otherwise a
        # test run leaves stale approvals in real people's notifications.
        if created_quotations:
            run(
                "delete from notifications where module = 'quotation'"
                " and entity_id = any(:ids)",
                ids=created_quotations,
            )
        if created_orders:
            run(
                "delete from notifications where module = 'sales_order'"
                " and entity_id = any(:ids)",
                ids=created_orders,
            )
        if created_quotations:
            run("delete from sales_quotation_activity where quotation_id = any(:ids)", ids=created_quotations)
            run("delete from sales_quotation where id = any(:ids)", ids=created_quotations)
        if created_orders:
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)
        session.commit()

        left = session.execute(
            text("select count(*) from sales_quotation where id = any(:ids)"),
            {"ids": created_quotations or [-1]},
        ).scalar()
        session.close()

        print(f"   removed {len(created_approvals)} approvals, {len(created_quotations)} quotations, {len(created_orders)} orders")
        print("   left behind:", left)
    except Exception as exc:
        print("   cleanup problem:", exc)

print(f"\n{len(passed)} passed, {len(failed)} failed")
for name in failed:
    print("  failed:", name)
sys.exit(1 if failed else 0)
