"""An order edited after it came from a proposal - the SO-02684 case.

A proposal for an OEM was quoted at 70,000 on a product whose transfer
price (DTP) is 68,000, so the line was marked up. On the order the
customer type was changed to End Customer, whose list price (ECP) is
79,000. The form kept showing 70,000 while the server stored 79,000, and
pressing Send For Approval on the edited draft only saved it.

This runs that sequence against the API:

  * an OEM order keeps a selling price above DTP, marked up;
  * moved to End Customer, the same line is priced at ECP, because 70,000
    is no longer above the list - which is what the form now shows too;
  * moved back with the mark-up, it keeps the selling price again;
  * an edited draft sent for approval leaves Draft and records it.

Then every seeded sales role (Area Managers, Zonal Heads, AVP, CEO,
Founder) edits a draft and presses Send For Approval, and the chain is
checked against the approval matrix: an OEM mark-up goes to the founder,
an end customer's 15% climbs past the raiser's ceiling, an undiscounted
order needs nobody and is confirmed. The founder signs one; another is
refused to a stranger, rejected, edited and sent again.

Everything it creates is removed again.

    docker exec -w /app backend_app python test_order_edit_resend.py
"""

import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.services import sales_order_service as sos
from seed_sales_team import TEAM_PASSWORD, api, email_for, login

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


created_orders, created_approvals = [], []

catalogue = sos._catalogue()
sku, entry = next(
    (
        (k, v) for k, v in catalogue.items()
        if (v.get("dtp") or 0) > 0 and (v.get("ecp") or 0) > (v.get("dtp") or 0) + 2000
    ),
    (None, None),
)
if sku is None:
    print("No catalogue product with DTP below ECP - nothing to test against.")
    sys.exit(1)

dtp, ecp = entry["dtp"], entry["ecp"]
selling = round(dtp + 1000, 2)  # above DTP, below ECP - like 70,000 on 68,000 / 79,000


def line(rate, marked_up, discount=0):
    return {
        "sku": sku, "product": "Interactive Flat Panel", "model": sku,
        "qty": 1, "rate": rate, "tax_rate": 18, "discount": discount, "marked_up": marked_up,
    }


def new_draft(who, label, customer_type, rate, marked_up, discount=0):
    r = api("post", "/orders", who, json={
        "customer_name": f"{label} {TAG}",
        "customer_type": customer_type,
        "order_date": datetime.now(timezone.utc).isoformat(),
        "status": "DRAFT",
        "items": [line(rate, marked_up, discount)],
    })
    if r.status_code >= 400:
        raise RuntimeError(f"create: {r.status_code} {r.text[:200]}")
    created_orders.append(r.json()["data"]["id"])
    return r.json()["data"]


def send_up(who, order, value):
    """What Send For Approval does (sendSalesOrderForApproval): ask for the
    chain, and confirm the order when nobody is on it."""
    r = api("post", "/approvals", who, json={
        "document_type": "SALES_ORDER",
        "document_id": order["id"],
        "document_number": order.get("order_number"),
        "price_type": "ECP",
        "discount_percent": 0,
        "document_value": value,
    })
    if r.status_code >= 400:
        raise RuntimeError(f"send: {r.status_code} {r.text[:200]}")
    approval = r.json().get("data")
    if approval:
        created_approvals.append(approval["id"])
        return approval
    r = api("post", f"/orders/{order['id']}/activities", who, json={
        "status": "CONFIRMED",
        "remarks": "No discount to approve, so the order was confirmed.",
    })
    if r.status_code >= 400:
        raise RuntimeError(f"confirm: {r.status_code} {r.text[:200]}")
    return None


try:
    admin = login(*ADMIN)
    salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
    print(f"\n{sku}: DTP {dtp}, ECP {ecp}, sold at {selling}")

    order = new_draft(salesperson, "Edit Resend", "OEM", selling, True)
    check("an Area Manager's OEM draft order is created", order["status"] == "DRAFT", order["status"])
    item = order["items"][0]
    check("it keeps the selling price above DTP, marked up",
          float(item["rate"]) == selling and item["marked_up"] is True, str(item))

    def edit(customer_type, rate, marked_up):
        r = api("put", f"/orders/{order['id']}", salesperson, json={
            "customer_type": customer_type, "items": [line(rate, marked_up)],
        })
        if r.status_code >= 400:
            raise RuntimeError(f"edit: {r.status_code} {r.text[:200]}")
        return r.json()["data"]

    print("\nCustomer type changed to End Customer")
    # What the old form sent: the proposal's price, still flagged.
    saved = edit("End Customer", selling, True)
    item = saved["items"][0]
    check("the server prices the line at ECP, since the selling price is no longer above it",
          float(item["rate"]) == ecp and item["marked_up"] is False, str(item))
    # What the form now sends after re-pricing the line (rebaseLinePrice).
    saved = edit("End Customer", ecp, False)
    check("the re-priced line the form now shows is exactly what is stored",
          float(saved["items"][0]["rate"]) == ecp and float(saved["total_amount"]) == ecp,
          f"{saved['items'][0]['rate']} / {saved['total_amount']}")

    print("\nBack to OEM with the mark-up")
    saved = edit("OEM", selling, True)
    item = saved["items"][0]
    check("the selling price above DTP is kept again",
          float(item["rate"]) == selling and item["marked_up"] is True
          and float(saved["total_amount"]) == selling, str(item))
    check("the edit leaves it a draft", saved["status"] == "DRAFT", saved["status"])

    from app.database.postgres import SessionLocal

    def history(order_id):
        with SessionLocal() as db:
            return [row[0] for row in db.execute(
                text("select action from sales_order_activity where sales_order_id = :id order by id"),
                {"id": order_id},
            )]

    print("\nSend For Approval on the edited draft")
    approval = send_up(salesperson, order, saved.get("grand_total") or 0)
    check("an approval is raised (a dealer-channel order goes up)", approval is not None)

    after = api("get", f"/orders/{order['id']}", admin).json()["data"]
    check("the order leaves Draft and waits on its approval",
          after["status"] == "PENDING_APPROVAL", after["status"])
    check("and its history says it was sent", "Sent For Approval" in history(order["id"]),
          str(history(order["id"])))

    print("\nSend For Approval when nobody has to sign")
    plain = new_draft(admin, "Edit Confirm", "End Customer", ecp, False)
    r = api("put", f"/orders/{plain['id']}", admin, json={"remarks": "edited before sending"})
    check("the draft is edited", r.status_code < 400, f"{r.status_code} {r.text[:160]}")
    approval = send_up(admin, plain, plain.get("grand_total") or 0)
    check("no approval is raised - it is within the sender's authority", approval is None, str(approval))
    after = api("get", f"/orders/{plain['id']}", admin).json()["data"]
    check("so the order is confirmed rather than left in Draft",
          after["status"] == "CONFIRMED", after["status"])

    # ------------------------------------------------------------------
    # Every sales role, editing a draft and pressing Send For Approval.
    # Who has to sign is the approval matrix's rule, as test_approval_chain
    # states it: a dealer-channel (DTP) order goes to the founder whatever
    # the discount; an end customer's 15% climbs past the AVP's ceiling;
    # an undiscounted end customer order needs nobody.
    # ------------------------------------------------------------------
    roles = ["am_north_1", "am_north_2", "am_south_1", "zh_north", "zh_south", "avp", "ceo", "founder"]
    token = {who: login(email_for(who), TEAM_PASSWORD) for who in roles}

    chain_15 = {
        "am_north_1": ["AVP", "CEO", "Founder"], "am_north_2": ["AVP", "CEO", "Founder"],
        "am_south_1": ["AVP", "CEO", "Founder"], "zh_north": ["AVP", "CEO", "Founder"],
        "zh_south": ["AVP", "CEO", "Founder"], "avp": ["CEO", "Founder"], "ceo": [], "founder": [],
    }

    def edit_and_send(who, label, customer_type, rate, marked_up, discount=0):
        draft = new_draft(token[who], label, customer_type, rate, marked_up, discount)
        r = api("put", f"/orders/{draft['id']}", token[who], json={"remarks": "edited before sending"})
        if r.status_code >= 400:
            raise RuntimeError(f"{who} edit: {r.status_code} {r.text[:200]}")
        approval = send_up(token[who], draft, r.json()["data"].get("grand_total") or 0)
        status = api("get", f"/orders/{draft['id']}", token[who]).json()["data"]["status"]
        return draft, approval, status

    for who in roles:
        print(f"\n{who}: edit a draft, then Send For Approval")

        expected = [] if who == "founder" else ["Founder"]
        draft, approval, status = edit_and_send(who, f"Role OEM {who}", "OEM", selling, True)
        got = [step["role"] for step in (approval or {}).get("steps", [])]
        check(f"{who}: an OEM mark-up goes to {expected or 'nobody'}", got == expected, str(got))
        check(f"{who}: and the order is {'held' if expected else 'confirmed'}",
              status == ("PENDING_APPROVAL" if expected else "CONFIRMED"), status)

        expected = chain_15[who]
        draft, approval, status = edit_and_send(who, f"Role 15 {who}", "End Customer", ecp, False, 15)
        got = [step["role"] for step in (approval or {}).get("steps", [])]
        check(f"{who}: an end customer's 15% climbs {expected or 'nothing'}", got == expected, str(got))
        check(f"{who}: and the order is {'held' if expected else 'confirmed'}",
              status == ("PENDING_APPROVAL" if expected else "CONFIRMED"), status)

        draft, approval, status = edit_and_send(who, f"Role 0 {who}", "End Customer", ecp, False)
        check(f"{who}: an undiscounted end customer order needs nobody and is confirmed",
              approval is None and status == "CONFIRMED", f"{approval and approval.get('steps')} / {status}")

    print("\nThe founder signs an Area Manager's OEM order")
    draft, approval, status = edit_and_send("am_north_1", "Signed OEM", "OEM", selling, True)
    r = api("put", f"/approvals/{approval['id']}/decide", token["founder"], json={"approve": True})
    check("the founder's approval is accepted", r.status_code < 400, f"{r.status_code} {r.text[:160]}")
    after = api("get", f"/orders/{draft['id']}", admin).json()["data"]
    check("the order is confirmed, still at the marked-up price",
          after["status"] == "CONFIRMED" and float(after["items"][0]["rate"]) == selling,
          f"{after['status']} {after['items'][0]['rate']}")

    print("\nRejected, edited and sent again")
    draft, approval, status = edit_and_send("am_north_1", "Rejected OEM", "OEM", selling, True)
    stranger = api("put", f"/approvals/{approval['id']}/decide", token["am_south_1"],
                   json={"approve": True})
    check("another Area Manager cannot decide it", stranger.status_code in (403, 400), str(stranger.status_code))
    r = api("put", f"/approvals/{approval['id']}/decide", token["founder"],
            json={"approve": False, "remarks": "Price too low for this channel."})
    check("the founder rejects it", r.status_code < 400, f"{r.status_code} {r.text[:160]}")
    after = api("get", f"/orders/{draft['id']}", token["am_north_1"]).json()["data"]
    check("the order comes back as a draft", after["status"] == "DRAFT", after["status"])
    r = api("put", f"/orders/{draft['id']}", token["am_north_1"],
            json={"items": [line(selling + 500, True)]})
    check("the Area Manager edits the price", r.status_code < 400, f"{r.status_code} {r.text[:160]}")
    again = send_up(token["am_north_1"], draft, r.json()["data"].get("grand_total") or 0)
    after = api("get", f"/orders/{draft['id']}", token["am_north_1"]).json()["data"]
    check("and Send For Approval raises a fresh request to the founder",
          again is not None and again["id"] != approval["id"]
          and [s["role"] for s in again["steps"]] == ["Founder"] and after["status"] == "PENDING_APPROVAL",
          f"{again and again.get('steps')} / {after['status']}")

except Exception as exc:  # noqa: BLE001 - reported as a failure, then cleaned up
    check("the run completes", False, repr(exc)[:200])

finally:
    print("\nClearing what the test created")
    try:
        from app.database.postgres import SessionLocal

        with SessionLocal() as db:
            if created_approvals:
                db.execute(text("delete from sales_approval where id = any(:ids)"), {"ids": created_approvals})
            if created_orders:
                db.execute(text("delete from sales_approval where document_type = 'SALES_ORDER' and document_id = any(:ids)"), {"ids": created_orders})
                db.execute(text("delete from notifications where module = 'sales_order' and entity_id = any(:ids)"), {"ids": created_orders})
                db.execute(text("delete from sales_order_activity where sales_order_id = any(:ids)"), {"ids": created_orders})
                db.execute(text("delete from sales_order where id = any(:ids)"), {"ids": created_orders})
            db.commit()
            left = db.execute(text("select count(*) from sales_order where id = any(:ids)"),
                              {"ids": created_orders or [-1]}).scalar()
        check("nothing is left behind", left == 0, str(left))
    except Exception as exc:  # noqa: BLE001
        print("   cleanup problem:", str(exc).split("\n")[0][:160])
        failed.append("cleanup")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
