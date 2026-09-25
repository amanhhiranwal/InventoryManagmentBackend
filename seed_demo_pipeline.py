"""Fill the CRM with a pipeline worth showing a team.

Six quotations raised by the Area Managers, sitting at every stage the
approval chain can reach:

    1  no discount            - nothing to approve, ready to send
    2  10% discount           - with the AVP
    3  18% discount           - the AVP has signed, now with the CEO
    4  25% discount           - AVP and CEO signed, now with the founder
    5  12% discount           - fully approved, released to send
    6  14% discount           - rejected, back to draft

So every state of the panel, the bell and the chain is on screen at once.

Run seed_sales_team.py first. Safe to run again: quotations it already
raised are left alone.

    docker exec -w /app backend_app python seed_demo_pipeline.py
"""

import sys
from datetime import datetime, timedelta, timezone

from seed_sales_team import TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")

#: owner, customer, contact, unit price, qty, discount %, how far to take it.
#: "steps" is how many approvals to grant; "reject" rejects at the first.
DEALS = [
    {
        "owner": "am_north_1", "customer": "Bluebells School",
        "contact": "Meera Kapoor", "city": "Gurgaon", "state": "Haryana",
        "price": 185000, "qty": 4, "discount": 0, "steps": 0,
    },
    {
        "owner": "am_north_1", "customer": "Greenfield Farms",
        "contact": "Ramesh Gupta", "city": "Delhi", "state": "Delhi",
        "price": 185000, "qty": 6, "discount": 10, "steps": 0,
    },
    {
        "owner": "am_north_2", "customer": "Riverside Agro",
        "contact": "Meena Joshi", "city": "Jaipur", "state": "Rajasthan",
        "price": 265000, "qty": 3, "discount": 18, "steps": 1,
    },
    {
        "owner": "am_north_2", "customer": "Horizon Public School",
        "contact": "Kavita Rao", "city": "Noida", "state": "Uttar Pradesh",
        "price": 185000, "qty": 10, "discount": 25, "steps": 2,
    },
    {
        "owner": "am_south_1", "customer": "Coastal Seeds",
        "contact": "Suresh Pillai", "city": "Kochi", "state": "Kerala",
        "price": 95000, "qty": 8, "discount": 12, "steps": 99,
    },
    {
        "owner": "am_south_1", "customer": "Sunrise Agro Industries",
        "contact": "Kavita Deshmukh", "city": "Pune", "state": "Maharashtra",
        "price": 265000, "qty": 5, "discount": 14, "steps": 0,
        "reject": True,
    },
]

#: Who holds each step, so the script can sign as the right person.
APPROVER_FOR = {"AVP": "avp", "CEO": "ceo", "Founder": None}


def main() -> int:
    admin = login(*ADMIN)
    token = {
        key: login(email_for(key), TEAM_PASSWORD)
        for key in ("avp", "ceo", "am_north_1", "am_north_2", "am_south_1")
    }
    token["Founder"] = admin

    # Once an order is approved it belongs to the desks, so the seed has to
    # sign in as them to push it along - exactly as the team will.
    token["accounts"] = login("accounts@mailinator.com", TEAM_PASSWORD)
    token["inventory"] = login("inventory@mailinator.com", TEAM_PASSWORD)

    existing = {
        row.get("organization_name")
        for row in rows(api("get", "/quotations/", admin))
    }

    companies = {
        c["company_code"]: c["id"]
        for c in rows(api("get", "/companies/", admin, params={"size": 200}))
    }

    print("Raising the pipeline\n")

    for deal in DEALS:
        if deal["customer"] in existing:
            print(f"  {deal['customer']} already there")
            continue

        owner = token[deal["owner"]]
        now = datetime.now(timezone.utc)

        # North sells for the North company, South for the South one, so
        # the two letterheads are both on show.
        code = "SYN-SOUTH" if deal["owner"].startswith("am_south") else "SYN-NORTH"

        created = api("post", "/quotations/", owner, json={
            "organization_name": deal["customer"],
            "contact_name": deal["contact"],
            "email": f"{deal['contact'].split()[0].lower()}.{deal['customer'].split()[0].lower()}@mailinator.com",
            "mobile_number": "+91 9812345670",
            "quotation_date": now.isoformat(),
            "validation_date": (now + timedelta(days=30)).isoformat(),
            "company_id": companies.get(code),
            "billing_address": {
                "street": f"{deal['city']} Industrial Area",
                "city": deal["city"],
                "state": deal["state"],
                "zipCode": "110001",
                "country": "India",
            },
            "items": [{
                "product": "Interactive Flat Panel",
                "model": "Qonevo IFP 75 - Core - 8/128",
                "sku": "NX-9K-QIFP75-EX",
                "quantity": deal["qty"],
                "unit_price": deal["price"],
                "discount": deal["discount"],
                "tax": 18,
            }],
        })

        if created.status_code >= 400:
            print(f"  {deal['customer']}: {created.status_code} {created.text[:120]}")
            continue

        quotation = created.json()["data"]
        subtotal = deal["price"] * deal["qty"]

        label = f"{deal['customer']} ({quotation['quote_number']})"

        if not deal["discount"]:
            print(f"  {label}: no discount, ready to send")
            continue

        raised = api("post", "/approvals", owner, json={
            "document_type": "QUOTATION",
            "document_id": quotation["id"],
            "document_number": quotation["quote_number"],
            "price_type": "ECP",
            "discount_percent": deal["discount"],
            "discount_amount": subtotal * deal["discount"] / 100,
            "document_value": quotation["total_payable"],
            "remarks": "Raised from the demo pipeline.",
        })

        if raised.status_code >= 400:
            print(f"  {label}: could not raise - {raised.text[:120]}")
            continue

        approval = raised.json()["data"]
        chain = [step["role"] for step in approval["steps"]]

        if deal.get("reject"):
            role = approval["waiting_on"]
            who = APPROVER_FOR.get(role) or "Founder"
            api("put", f"/approvals/{approval['id']}/decide", token[who], json={
                "approve": False,
                "remarks": "Margin is too thin on this account.",
            })
            print(f"  {label}: {deal['discount']}% rejected by the {role}")
            continue

        granted = 0

        for _ in range(min(deal["steps"], len(chain))):
            current = api(
                "get", f"/approvals/document/QUOTATION/{quotation['id']}", admin
            ).json()["data"]

            if current["status"] != "PENDING":
                break

            role = current["waiting_on"]
            who = APPROVER_FOR.get(role) or "Founder"

            step = api("put", f"/approvals/{current['id']}/decide", token[who], json={
                "approve": True,
                "remarks": f"Approved by the {role}.",
            })

            if step.status_code >= 400:
                print(f"  {label}: {role} could not sign - {step.text[:100]}")
                break

            granted += 1

        final = api(
            "get", f"/approvals/document/QUOTATION/{quotation['id']}", admin
        ).json()["data"]

        where = (
            f"waiting on the {final['waiting_on']}"
            if final["status"] == "PENDING"
            else final["status"].lower()
        )
        print(
            f"  {label}: {deal['discount']}% -> {' > '.join(chain)}"
            f", {granted} signed, {where}"
        )

    fulfilment(token, admin, companies)

    print("\nWhat the team will see")

    for row in rows(api("get", "/quotations/", admin)):
        print(f"  {row['quote_number']:<10} {row.get('organization_name', ''):<26} {row['status']}")

    for row in rows(api("get", "/orders", admin)):
        print(f"  {str(row.get('order_number') or row['id']):<10} {row.get('customer_name', ''):<26} {row['status']}")

    return 0


#: The journey past the quotation: an order, an invoice, the advance, and
#: however far into fulfilment each one has got. Between them they put
#: every stage of the chain on screen at once.
ORDERS = [
    {
        "owner": "am_north_1", "customer": "Bluebells School",
        "state": "Haryana", "price": 185000, "qty": 4,
        "paid": None, "stage": None,
        "note": "confirmed, invoice raised, waiting on the advance",
    },
    {
        "owner": "am_north_2", "customer": "Riverside Agro",
        "state": "Rajasthan", "price": 265000, "qty": 3,
        "paid": "advance", "stage": "PROCUREMENT",
        "note": "advance in, with inventory",
    },
    {
        "owner": "am_south_1", "customer": "Coastal Seeds",
        "state": "Kerala", "price": 95000, "qty": 8,
        "paid": "advance", "stage": "INSTALLED",
        "note": "installed, balance still owed",
    },
]


def fulfilment(token, admin, companies):
    """Carry three deals past the quotation and into fulfilment.

    The approval chain was already on show; what came after it was not -
    there were no invoices at all, so nobody could see what recording a
    payment does to an order.
    """

    print("\nCarrying deals into fulfilment\n")

    existing = {
        row.get("customer_name")
        for row in rows(api("get", "/orders", admin))
    }

    for deal in ORDERS:
        if deal["customer"] in existing:
            print(f"  {deal['customer']} already has an order")
            continue

        owner = token[deal["owner"]]
        now = datetime.now(timezone.utc)

        created = api("post", "/orders", owner, json={
            "customer_name": deal["customer"],
            "company_name": (
                "Synergy South Seeds"
                if deal["owner"].startswith("am_south")
                else "Synergy North Agro"
            ),
            "state": deal["state"],
            "order_date": now.isoformat(),
            "items": [{
                "product": "Interactive Flat Panel",
                "model": "Qonevo IFP 75 - Core - 8/128",
                "sku": "NX-9K-QIFP75-EX",
                "qty": deal["qty"],
                "rate": deal["price"],
                "tax_rate": 18,
            }],
        })

        if created.status_code >= 400:
            print(f"  {deal['customer']}: {created.status_code} {created.text[:120]}")
            continue

        order = created.json()["data"]
        reference = order.get("order_number") or order["id"]

        # An invoice can only go on a confirmed order.
        api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})

        raised = api("post", "/proforma-invoices", owner, json={
            "sales_order_id": order["id"],
            "status": "DRAFT",
            "advance_percent": 30,
        })

        if raised.status_code >= 400:
            print(f"  {reference}: no invoice - {raised.text[:120]}")
            continue

        invoice = raised.json()["data"]
        api("post", f"/proforma-invoices/{invoice['id']}/generate", owner)

        if deal["paid"] == "advance":
            # Sales record what came in; accounts are the ones who say it
            # arrived, which is what releases the order to inventory.
            api("put", f"/proforma-invoices/{invoice['id']}", owner, json={
                "amount_paid": round(float(invoice["grand_total"]) * 0.3, 2),
            })

            api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
                "approve": True,
                "remarks": "Advance received against the proforma invoice.",
            })

        # Inventory walk it the rest of the way; the installation sign-off
        # is the salesperson's, being the one on site.
        for step, who in (
            ("PROCUREMENT", "inventory"),
            ("READY", "inventory"),
            ("DISPATCHED", "inventory"),
            ("DELIVERED", "inventory"),
            ("INSTALLED", deal["owner"]),
        ):
            if not deal["stage"]:
                break

            api("put", f"/orders/{order['id']}/status", token[who], json={"status": step})

            if step == deal["stage"]:
                break

        final = api("get", f"/orders/{order['id']}", admin).json()["data"]

        print(
            f"  {reference} {deal['customer']}: {final['status']} "
            f"({invoice['pi_number']}) - {deal['note']}"
        )


if __name__ == "__main__":
    sys.exit(main())
