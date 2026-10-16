"""The discount approval chain, run by the seeded Sales team.

Approval sits on the sales order. A proposal is a price put in front of a
customer to see what they say, and needs no signature to go out; the
order is the commitment, and that is what climbs the chain.

Run seed_sales_team.py first. An Area Manager raises orders at four
discount levels and checks each goes to the right people:

     0%  ->  nobody
    10%  ->  their own AVP, and nobody else's
    18%  ->  the AVP, then the CEO
    25%  ->  the AVP, the CEO, then the founder

A dealer order goes to the founder whatever the discount, because the
question there is not how much has been given away but whether we sell
through the channel at all.

It also checks that a proposal needs no approval and can be emailed
without one, that the order is held while the chain runs and released
when it clears, that a rejection hands it back, and that another zone's
deal is not yours to approve.

The last section opens the approval email itself and clicks Approve in
it, the way the person who receives it would: the buttons have to be in
the first ask, decide without a session, work once, and refuse a token
whose signature has been altered.

Everything it creates is removed again.

    docker exec -w /app backend_app python test_approval_chain.py
"""

import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

from seed_sales_team import BASE, TEAM_PASSWORD, api, email_for, login, rows

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
    everyone = [
        "founder", "ceo", "avp", "zh_north", "zh_south",
        "am_north_1", "am_south_1",
    ]

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

    check("the AVP carries the first 10%", bands["AVP"]["to_percent"] == 10.0, str(bands.get("AVP")))
    check("the CEO carries up to 20%", bands["CEO"]["to_percent"] == 20.0, str(bands.get("CEO")))
    check("past that it is the founder", bands["Founder"]["to_percent"] is None, str(bands.get("Founder")))

    def preview(discount, price_type="ECP", document_type="SALES_ORDER"):
        return api(
            "get", "/approvals/preview", token["am_north_1"],
            params={
                "price_type": price_type,
                "discount_percent": discount,
                "document_type": document_type,
            },
        ).json()["data"]

    # Every sales order ends with the founder: the bands say who owns the
    # discount, the founder commits the business to the order. An order at
    # list price used to need nobody at all, which meant it was confirmed
    # the moment it was raised.
    check(
        "an undiscounted order still goes to the founder",
        preview(0)["chain"] == ["Founder"],
        str(preview(0)["chain"]),
    )
    check("5% stops at the AVP", preview(5)["chain"] == ["AVP", "Founder"], str(preview(5)["chain"]))
    check("10% still stops at the AVP", preview(10)["chain"] == ["AVP", "Founder"], str(preview(10)["chain"]))
    check("12% reaches the CEO", preview(12)["chain"] == ["AVP", "CEO", "Founder"], str(preview(12)["chain"]))
    check("20% still stops at the CEO", preview(20)["chain"] == ["AVP", "CEO", "Founder"], str(preview(20)["chain"]))
    check(
        "25% reaches the founder",
        preview(25)["chain"] == ["AVP", "CEO", "Founder"],
        str(preview(25)["chain"]),
    )

    # The dealer question is not how much has been given away.
    check(
        "an undiscounted dealer order goes to the founder",
        preview(0, "DP")["chain"] == ["Founder"],
        str(preview(0, "DP")["chain"]),
    )
    check(
        "and a discounted one goes to the founder too",
        preview(30, "DP")["chain"] == ["Founder"],
        str(preview(30, "DP")["chain"]),
    )

    # A proposal is not a commitment, so nothing signs it.
    check(
        "a proposal needs no approval, whatever the discount",
        preview(25, "ECP", "QUOTATION")["chain"] == [],
        str(preview(25, "ECP", "QUOTATION")["chain"]),
    )
    check(
        "and the reason says where the approval went",
        "sales order" in preview(25, "ECP", "QUOTATION")["reason"].lower(),
        preview(25, "ECP", "QUOTATION")["reason"],
    )

    # ------------------------------------------------------- a quotation
    banner("2. An Area Manager raises a discounted sales order")

    customer_types = {
        row["name"]: row["id"]
        for row in rows(api("get", "/customer-types/", admin))
    }

    def make_order(label, discount, customer_type=None, who="am_north_1"):
        """An order on the real catalogue, so the server prices it itself."""

        payload = {
            "customer_name": f"{label} {TAG}",
            "order_date": datetime.now(timezone.utc).isoformat(),
            "items": [
                {
                    "sku": "SG-IFP-65-SPX-V100",
                    "product": "Interactive Flat Panel",
                    "model": "65\" SPX",
                    "qty": 10,
                    "rate": 0,
                    "tax_rate": 18,
                    "discount": discount,
                }
            ],
        }

        if customer_type:
            payload["customer_type"] = customer_type

        r = api("post", "/orders", token[who], json=payload)

        if r.status_code >= 400:
            raise RuntimeError(f"{label}: {r.status_code} {r.text[:200]}")

        order = r.json()["data"]
        created_orders.append(order["id"])
        return order

    def send_up(order, discount, who="am_north_1", price_type="ECP"):
        return api("post", "/approvals", token[who], json={
            "document_type": "SALES_ORDER",
            "document_id": order["id"],
            "document_number": order.get("order_number"),
            "price_type": price_type,
            "discount_percent": discount,
            "document_value": order.get("grand_total") or 0,
        })

    # 18%: past the AVP's 10%, so the AVP and then the CEO.
    order = make_order("Banded Discount", 18)

    held = api("put", f"/orders/{order['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "a discounted order cannot be confirmed unapproved",
        held.status_code == 409,
        f"got {held.status_code} {held.text[:140]}",
    )
    check(
        "and the refusal names who has to sign",
        "AVP" in held.text and "CEO" in held.text,
        held.text[:160],
    )

    r = send_up(order, 18)
    check("18% is sent up for approval", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    approval = r.json().get("data")
    if approval:
        created_approvals.append(approval["id"])

    check("it is waiting on the AVP first", (approval or {}).get("waiting_on") == "AVP", str((approval or {}).get("waiting_on")))
    check(
        "every step is recorded up front, the founder last",
        [step["role"] for step in (approval or {}).get("steps", [])]
        == ["AVP", "CEO", "Founder"],
        str((approval or {}).get("steps")),
    )

    # ------------------------------------------------- who may decide it
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
        "approve": True, "remarks": "Within my 10%, passing the rest up.",
    })
    check("the AVP approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    after_avp = r.json()["data"] if r.status_code == 200 else {}
    check("it now waits on the CEO", after_avp.get("waiting_on") == "CEO", str(after_avp.get("waiting_on")))
    check("it is still pending overall", after_avp.get("status") == "PENDING", str(after_avp.get("status")))
    check(
        "the AVP's decision is recorded against them",
        (after_avp.get("steps") or [{}])[0].get("approver_name"),
        str((after_avp.get("steps") or [{}])[0]),
    )

    half_way = api("put", f"/orders/{order['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "the order is still held half way up the chain",
        half_way.status_code == 409,
        f"got {half_way.status_code} {half_way.text[:140]}",
    )

    check("it has moved into the CEO's queue", approval["id"] in pending_ids("ceo"))

    r = api("put", f"/approvals/{approval['id']}/decide", token["ceo"], json={
        "approve": True, "remarks": "Approved.",
    })
    check("the CEO approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    after_ceo = r.json()["data"] if r.status_code == 200 else {}
    check(
        "it now waits on the founder, who commits every order",
        after_ceo.get("waiting_on") == "Founder",
        str(after_ceo.get("waiting_on")),
    )
    check(
        "still not approved on the CEO's signature alone",
        after_ceo.get("status") == "PENDING",
        str(after_ceo.get("status")),
    )

    not_yet = api("put", f"/orders/{order['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "and the order is still held",
        not_yet.status_code == 409,
        f"got {not_yet.status_code} {not_yet.text[:140]}",
    )

    r = api("put", f"/approvals/{approval['id']}/decide", token["founder"], json={
        "approve": True, "remarks": "Committed.",
    })
    check("the founder approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    final = r.json()["data"] if r.status_code == 200 else {}
    check("the request is fully approved", final.get("status") == "APPROVED", str(final.get("status")))
    check("nothing is waiting on anyone", final.get("waiting_on") is None, str(final.get("waiting_on")))

    released = api("put", f"/orders/{order['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "and the order can now be confirmed",
        released.status_code == 200,
        f"{released.status_code} {released.text[:140]}",
    )

    # ------------------------------------------------------- a rejection
    banner("5. A rejection hands it back")

    rejected = make_order("Rejected Discount", 18)
    r = send_up(rejected, 18)
    rejection = r.json().get("data")

    if rejection:
        created_approvals.append(rejection["id"])

    r = api("put", f"/approvals/{rejection['id']}/decide", token["avp"], json={
        "approve": False, "remarks": "Too deep for this account.",
    })
    check("the AVP rejects it", r.status_code == 200, f"{r.status_code} {r.text[:140]}")

    done = r.json()["data"] if r.status_code == 200 else {}
    check("the request is rejected", done.get("status") == "REJECTED", str(done.get("status")))
    check("nobody is waiting on it", done.get("waiting_on") is None, str(done.get("waiting_on")))
    check(
        "it is out of the CEO's queue",
        rejection["id"] not in pending_ids("ceo"),
    )

    blocked = api("put", f"/orders/{rejected['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "a rejected order still cannot be confirmed",
        blocked.status_code == 409,
        f"got {blocked.status_code} {blocked.text[:140]}",
    )

    # --------------------------------------------------- no approval due
    banner("6. A proposal is not signed off, and goes out anyway")

    # An order at list price gives nothing away, but it is still a
    # commitment - so it goes to the founder and nowhere else.
    plain = make_order("No Discount", 0)
    r = api("put", f"/orders/{plain['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "even an undiscounted order cannot be confirmed unapproved",
        r.status_code == 409,
        f"{r.status_code} {r.text[:140]}",
    )

    r = send_up(plain, 0)
    check("it is sent up", r.status_code == 200, f"{r.status_code} {r.text[:140]}")

    plain_approval = r.json().get("data")

    if plain_approval:
        created_approvals.append(plain_approval["id"])

    check(
        "with the founder as the only step",
        [s["role"] for s in (plain_approval or {}).get("steps", [])] == ["Founder"],
        str((plain_approval or {}).get("steps")),
    )

    r = api("put", f"/approvals/{plain_approval['id']}/decide", token["founder"], json={
        "approve": True,
    })
    check("the founder commits it", r.status_code == 200, f"{r.status_code} {r.text[:140]}")

    r = api("put", f"/orders/{plain['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check("and then it confirms", r.status_code == 200, f"{r.status_code} {r.text[:140]}")

    # A proposal at 25% - which on an order would need all three - needs
    # nothing, because the commitment is the order.
    quotation = api("post", "/quotations/", token["am_north_1"], json={
        "organization_name": f"Unsigned Proposal {TAG}",
        "contact_name": "Approval Test",
        "email": f"proposal.{TAG}@mailinator.com",
        "mobile_number": "+91 9812345670",
        "quotation_date": datetime.now(timezone.utc).isoformat(),
        "validation_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "items": [{"sku": "SG-IFP-65-SPX-V100", "product": "Interactive Flat Panel",
                   "model": "65\" SPX", "quantity": 10, "unit_price": 0,
                   "discount": 25, "tax": 18}],
    })
    check("a proposal at 25% is raised", quotation.status_code == 200, quotation.text[:140])

    if quotation.status_code == 200:
        q = quotation.json()["data"]
        created_quotations.append(q["id"])

        check("it is not held for approval", q.get("status") == "DRAFT", str(q.get("status")))

        # No recipient on purpose: nothing is sent, but the refusal says
        # whether approval is still being demanded first.
        sent = api("post", f"/quotations/{q['id']}/send", token["am_north_1"], json={"to": []})
        check(
            "and the only thing stopping it being emailed is a recipient",
            sent.status_code == 400 and "recipient" in sent.text.lower(),
            f"{sent.status_code} {sent.text[:140]}",
        )

    # ------------------------------------------------------ dealer price
    banner("7. A dealer order is the founder's, discount or not")

    dealer = make_order("Dealer Transfer", 0, customer_type="Dealer")

    r = api("put", f"/orders/{dealer['id']}/status", token["am_north_1"], json={
        "status": "CONFIRMED",
    })
    check(
        "an undiscounted dealer order is still held",
        r.status_code == 409,
        f"got {r.status_code} {r.text[:140]}",
    )
    check("and it is the founder being waited on", "Founder" in r.text, r.text[:160])

    r = send_up(dealer, 0, price_type="DP")
    check("it is sent up", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    dealer_approval = r.json().get("data")
    if dealer_approval:
        created_approvals.append(dealer_approval["id"])

    check(
        "straight to the founder, with nobody below",
        [step["role"] for step in (dealer_approval or {}).get("steps", [])] == ["Founder"],
        str((dealer_approval or {}).get("steps")),
    )
    check(
        "the AVP is not asked about a transfer price",
        (dealer_approval or {}).get("waiting_on") == "Founder",
        str((dealer_approval or {}).get("waiting_on")),
    )

    # ------------------------------------------------ the deepest cut
    banner("7b. The deepest cut decides, not the blended share")

    # 25% on one cheap line and nothing on an expensive one blends to
    # almost nothing. The band is decided by the deepest line, because
    # that is the one somebody actually gave away.
    lopsided = api("post", "/orders", token["am_north_1"], json={
        "customer_name": f"Lopsided {TAG}",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [
            {"sku": "SG-IFP-65-SPX-V100", "product": "Panel", "model": "65",
             "qty": 20, "rate": 0, "tax_rate": 18, "discount": 0},
            {"sku": "SG-IFP-75-SPX-V100", "product": "Panel", "model": "75",
             "qty": 1, "rate": 0, "tax_rate": 18, "discount": 25},
        ],
    })
    check("a lopsided order is raised", lopsided.status_code == 200, lopsided.text[:140])

    if lopsided.status_code == 200:
        lop = lopsided.json()["data"]
        created_orders.append(lop["id"])

        r = api("put", f"/orders/{lop['id']}/status", token["am_north_1"], json={
            "status": "CONFIRMED",
        })
        check(
            "the 25% line holds the whole order",
            r.status_code == 409,
            f"got {r.status_code} {r.text[:140]}",
        )
        check(
            "and it goes all the way to the founder",
            "Founder" in r.text,
            r.text[:170],
        )

    # ------------------------------------------- somebody else's region
    banner("8. Another zone's deal is not yours to approve")

    southern = make_order("Southern Deal", 18, who="am_south_1")
    r = send_up(southern, 18, who="am_south_1")
    southern_approval = r.json().get("data")

    if southern_approval:
        created_approvals.append(southern_approval["id"])

    check(
        "the southern order is waiting on an AVP",
        (southern_approval or {}).get("waiting_on") == "AVP",
        str((southern_approval or {}).get("waiting_on")),
    )

    # The seeded team has one AVP over both zones, so this checks the
    # rule that holds either way: whoever it is waiting on is somebody in
    # that salesperson's own line, never an unrelated manager.
    approvers = rows(api("get", "/approvals/pending", token["avp"]))
    check(
        "it is in their own AVP's queue",
        southern_approval["id"] in {a["id"] for a in approvers},
        str([a["id"] for a in approvers])[:120],
    )
    check(
        "a Zonal Head from the other zone cannot touch it",
        api(
            "put",
            f"/approvals/{southern_approval['id']}/decide",
            token["zh_north"],
            json={"approve": True},
        ).status_code
        == 403,
    )

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

        # The founder commits every order, so the walk starts by getting
        # that signature rather than setting Confirmed by hand.
        raised = send_up(order, 0)
        check(
            "it goes up for approval first",
            raised.status_code == 200,
            f"{raised.status_code} {raised.text[:140]}",
        )

        walk_approval = raised.json().get("data")

        if walk_approval:
            created_approvals.append(walk_approval["id"])

            signed = api(
                "put",
                f"/approvals/{walk_approval['id']}/decide",
                token["founder"],
                json={"approve": True},
            )
            check(
                "the founder commits it",
                signed.status_code == 200,
                f"{signed.status_code} {signed.text[:140]}",
            )

        # Who may push each step: the sales owner up to Confirmed, then the
        # desks, because once an order is approved it stops being theirs.
        chain = [
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

    # ------------------------------------------- approving from the email
    # ------------------------------------- the screen's own flow, per role
    banner("9c. Send For Approval, from each desk in turn")

    # What the New Sales Order screen does: save the order, then send it
    # up. It used to create the order CONFIRMED and raise nothing, so the
    # button said "Send For Approval" and the row said Approved - with no
    # chain, no signature, and nothing in anybody's queue.
    born_confirmed = api("post", "/orders", token["am_north_1"], json={
        "customer_name": f"Born Confirmed {TAG}",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "status": "CONFIRMED",
        "items": [{"product": "Panel", "qty": 1, "rate": 50000, "tax_rate": 18}],
    })
    check(
        "an order cannot be created already confirmed",
        born_confirmed.status_code == 400,
        f"got {born_confirmed.status_code} {born_confirmed.text[:120]}",
    )

    if born_confirmed.status_code == 200:
        created_orders.append(born_confirmed.json()["data"]["id"])

    # Each desk raises one at 15%, which is past the AVP's ceiling.
    for who, expected in [
        ("am_north_1", ["AVP", "CEO", "Founder"]),
        ("zh_north", ["AVP", "CEO", "Founder"]),
        ("avp", ["CEO", "Founder"]),
        ("ceo", ["Founder"]),
        ("founder", []),
    ]:
        their_order = make_order(f"Desk {who}", 15, who=who)

        check(
            f"{who}: the order is raised as a draft",
            their_order.get("status") == "DRAFT",
            str(their_order.get("status")),
        )

        sent = send_up(their_order, 15, who=who)
        raised = (sent.json() or {}).get("data") if sent.status_code == 200 else None

        if raised:
            created_approvals.append(raised["id"])

        got = [step["role"] for step in (raised or {}).get("steps", [])]

        check(
            f"{who}: it climbs {expected or 'nothing - they are the top'}",
            got == expected,
            f"got {got}",
        )

        after = api("get", f"/orders/{their_order['id']}", token[who]).json()["data"]

        check(
            f"{who}: the order is held, not approved"
            if expected
            else f"{who}: nothing to approve, so it is theirs to confirm",
            after["status"] == ("PENDING_APPROVAL" if expected else "DRAFT"),
            str(after["status"]),
        )

        # And it is their own line that is asked, not every holder.
        if raised:
            waiting = raised.get("waiting_on")
            queue_holder = {
                "AVP": "avp", "CEO": "ceo", "Founder": "founder",
            }.get(waiting)

            if queue_holder:
                check(
                    f"{who}: it is in the {waiting}'s queue",
                    raised["id"] in pending_ids(queue_holder),
                    f"waiting on {waiting}",
                )

            for stranger in ("am_south_1", "zh_south"):
                check(
                    f"{who}: and not in {stranger}'s",
                    raised["id"] not in pending_ids(stranger),
                )

    # ------------------------------------------- rejected, then resubmitted
    banner("9d. A rejected order comes back with its history")

    second = make_order("Round Two", 18)
    sent = send_up(second, 18)
    first_round = (sent.json() or {}).get("data")

    if first_round:
        created_approvals.append(first_round["id"])

    turned_down = api(
        "put", f"/approvals/{first_round['id']}/decide", token["avp"],
        json={"approve": False, "remarks": "Drop it to 12% and come back."},
    )
    check(
        "the AVP rejects it with a reason",
        turned_down.status_code == 200,
        f"{turned_down.status_code} {turned_down.text[:120]}",
    )

    # Raising it again is what a resubmission is: a second approval on the
    # same order.
    again = send_up(second, 12)
    second_round = (again.json() or {}).get("data")

    if second_round:
        created_approvals.append(second_round["id"])

    check(
        "it can be sent up again",
        again.status_code == 200 and second_round is not None,
        f"{again.status_code} {again.text[:140]}",
    )

    # What the screen asks for is where the order stands now, and after a
    # resubmission that is the new request - not the refusal behind it.
    # Reading the state off the document's whole approval history was how
    # a fresh request came up wearing an older round's decision.
    current = api(
        "get", f"/approvals/document/SALES_ORDER/{second['id']}",
        token["am_north_1"],
    ).json().get("data")

    check(
        "the order now stands on the new request",
        current and current["id"] == second_round["id"],
        str(current and current["id"]),
    )
    check(
        "which is waiting for a decision, not carrying the old one",
        current and current["status"] == "PENDING",
        str(current and current["status"]),
    )

    # ------------------------------------- changing it sends it back up
    banner("9f. A deleted order does not leave its approval behind")

    # Ids come from a sequence, so an approval that outlives its order is
    # waiting to be read as the approval of whatever order is handed that
    # id next - and a brand new order then comes up already approved with
    # no step for the CEO or the founder in it. That is what a live
    # database was doing after a few rounds of testing.
    doomed = make_order("Deleted After Approval", 0)
    doomed_approval = (send_up(doomed, 0).json() or {}).get("data")

    check(
        "an order can be sent up and then deleted",
        doomed_approval is not None
        and api("delete", f"/orders/{doomed['id']}", admin).status_code < 400,
        str(doomed_approval and doomed_approval["id"]),
    )

    from app.database.postgres import SessionLocal as _Session
    from app.models.approval import SalesApproval as _Approval

    _db = _Session()

    left = (
        _db.query(_Approval)
        .filter(_Approval.id == (doomed_approval or {}).get("id"))
        .first()
    )

    _db.close()

    check(
        "its approval goes with it, rather than waiting for the next order",
        left is None,
        "the approval outlived the order",
    )

    banner("9e. Changing an approved order sends it back up the chain")

    settled = make_order("Edited After Approval", 0)
    raised = send_up(settled, 0)
    settled_approval = (raised.json() or {}).get("data")

    if settled_approval:
        created_approvals.append(settled_approval["id"])
        api(
            "put", f"/approvals/{settled_approval['id']}/decide",
            token["founder"], json={"approve": True},
        )

    api("put", f"/orders/{settled['id']}/status", token["am_north_1"],
        json={"status": "CONFIRMED"})

    confirmed = api("get", f"/orders/{settled['id']}", token["am_north_1"]).json()["data"]
    check("it is approved and confirmed", confirmed["status"] == "CONFIRMED",
          str(confirmed["status"]))

    # An attachment is not a change to the commitment.
    api("put", f"/orders/{settled['id']}", token["am_north_1"],
        json={"attachments": [{"name": "site-photo.pdf"}]})

    after_file = api("get", f"/orders/{settled['id']}", token["am_north_1"]).json()["data"]
    check(
        "attaching a document leaves it approved",
        after_file["status"] == "CONFIRMED",
        str(after_file["status"]),
    )

    # The discount is.
    api("put", f"/orders/{settled['id']}", token["am_north_1"],
        json={"discount_mode": "PERCENT", "discount_input": 30})

    after_edit = api("get", f"/orders/{settled['id']}", token["am_north_1"]).json()["data"]
    check(
        "changing the discount sends it back to draft",
        after_edit["status"] == "DRAFT",
        str(after_edit["status"]),
    )
    check(
        "and it cannot be confirmed again without approval",
        api("put", f"/orders/{settled['id']}/status", token["am_north_1"],
            json={"status": "CONFIRMED"}).status_code == 409,
    )

    banner("9b. The approval email decides it without signing in")

    # Built here rather than driven over HTTP, because what is being
    # checked is the letter itself - the two buttons only exist in the
    # message, and the server has already sent it by the time an API
    # response comes back. The mailer is replaced first so nothing is
    # actually posted.
    from app.database.postgres import SessionLocal as _Session
    from app.models.user import User as _User
    from app.services.approval_service import ApprovalService as _Approvals
    from app.services.email_service import EmailService as _Mailer

    letter = {}

    def _capture(to, subject, text_body, html_body=None, cc=None, **kw):
        letter.update({"to": to, "html": html_body or ""})
        return True

    _sent_for_real = _Mailer.send
    _Mailer.send = staticmethod(_capture)

    try:
        mail_order = make_order("Email Approval", 18)
        session = _Session()
        raiser = session.query(_User).filter(
            _User.email == email_for("am_north_1")
        ).first()

        mail_approval = _Approvals.request(
            "SALES_ORDER",
            mail_order["id"],
            document_number=mail_order.get("order_number"),
            price_type="ECP",
            discount_percent=18,
            discount_amount=None,
            orc_percent=None,
            orc_amount=None,
            document_value=mail_order.get("grand_total") or 0,
            remarks=None,
            current_user={"user_id": str(raiser.id)},
            db=session,
        )
        created_approvals.append(mail_approval.id)

        # The links are built from the CRM Address in Masters, which on a
        # real database points at the live site. A test must open its own
        # server and nothing else, so each one is pulled back onto BASE.
        links = [
            BASE.rsplit("/api/v1", 1)[0] + "/api/v1" + found.split("/api/v1", 1)[1]
            for found in re.findall(
                r'href="([^"]*decide-by-link[^"]*)"', letter.get("html", "")
            )
        ]

        check(
            "the first ask carries Approve and Reject",
            len(links) == 2,
            f"found {len(links)} in the letter to {letter.get('to')}",
        )
        check(
            "and it went to the one person the step names",
            letter.get("to") == [email_for("avp")],
            str(letter.get("to")),
        )

        if len(links) == 2:
            approve_link, reject_link = links

            # Opened the way a mail client would: no session, no header.
            landed = requests.get(approve_link, timeout=20)
            check(
                "opening the link decides it",
                landed.status_code == 200
                and "has been approved" in landed.text,
                f"{landed.status_code} {landed.text[:120]}",
            )

            session.expire_all()
            after = session.get(type(mail_approval), mail_approval.id)
            check(
                "the AVP's step is signed, and says where from",
                after.steps[0]["decision"] == "APPROVED"
                and "email" in (after.steps[0].get("remarks") or "").lower(),
                str(after.steps[0]),
            )
            check(
                "and it has moved on to the CEO",
                after.current_step == 1,
                f"current step {after.current_step}",
            )

            again = requests.get(approve_link, timeout=20)
            check(
                "the same link cannot be used twice",
                "cannot be used" in again.text,
                again.text[:120],
            )
            check(
                "and neither can the Reject half of a spent mail",
                "cannot be used" in requests.get(reject_link, timeout=20).text,
            )

            tampered = approve_link[:-2] + ("aa" if approve_link[-2:] != "aa" else "bb")
            check(
                "a token with the signature altered is refused",
                "no longer valid" in requests.get(tampered, timeout=20).text,
            )

        # Every link the CRM emails has to agree on where the CRM is. The
        # password reset used to be built from the environment instead,
        # so a site that had moved sent approvals to the right address
        # and resets to the old one.
        from app.services.company_profile_service import app_url as _base
        from app.services.password_reset_service import PasswordResetService

        reset = PasswordResetService._build_reset_link("T", "a@b.com", session)
        check(
            "a password reset points at the same place as an approval",
            reset.startswith(_base("", session)) and "/reset-password?" in reset,
            reset,
        )
    finally:
        _Mailer.send = _sent_for_real

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
