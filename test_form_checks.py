"""The shapes the forms check, checked again by the server.

Sales orders, opportunities and proposals refuse a malformed GSTIN, PAN,
mobile number, email or Indian PIN however they arrive. Empty fields are
left alone - a draft is work in progress - and a foreign postcode is not
held to six digits. A registered organisation cannot be sent for approval,
or confirmed, without a PAN; it can still be saved as a draft.

Run seed_sales_team.py first. The orders it creates are removed again.

    docker exec -w /app backend_app python test_form_checks.py
"""

import sys
import uuid
from datetime import datetime, timezone

from seed_sales_team import TEAM_PASSWORD, api, email_for, login, sign_off_order

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


created_orders: list[int] = []
created_opportunities: list[int] = []
created_quotations: list[int] = []

try:
    owner = login(email_for("am_north_1"), TEAM_PASSWORD)
    now = datetime.now(timezone.utc).isoformat()

    def order(**overrides):
        body = {
            "customer_name": f"Checks {TAG}",
            "company_name": f"Checks School {TAG}",
            "order_date": now,
            "items": [{"product": "Panel", "qty": 1, "rate": 1000, "tax_rate": 18}],
            **overrides,
        }
        r = api("post", "/orders", owner, json=body)

        if r.status_code == 200:
            created_orders.append(r.json()["data"]["id"])

        return r

    def refused(r, words):
        return r.status_code == 400 and words.lower() in r.text.lower()

    # ================================================== sales orders
    banner("1. A sales order refuses numbers of the wrong shape")

    check("a short PAN", refused(order(customer_information={"registration": "Registered", "pan": "ABCDE123"}), "five letters"))
    check("a GSTIN of the wrong length", refused(order(customer_information={"gst": "09ABCDE1234"}), "15"))
    check("a nine-digit phone", refused(order(customer_information={"primary_contact": {"phone": "987654321"}}), "10"))
    check("an email with no domain", refused(order(customer_information={"primary_contact": {"email": "ramesh@"}}), "not a valid email"))
    check("a five-digit billing PIN", refused(order(billing_address={"country": "India", "pin": "20130"}), "Billing PIN"))
    check("a PIN opening with 0 on shipping", refused(order(shipping_address={"country": "India", "pin": "012345"}), "Shipping PIN"))

    r = order(
        customer_information={
            "registration": "Registered",
            "gst": "09AAACQ7899Q1Z6",
            "pan": "AAACQ7899Q",
            "primary_contact": {"phone": "+91 98765 43210", "email": "ramesh@mailinator.com"},
        },
        billing_address={"country": "India", "pin": "201301"},
        shipping_address={"country": "United States", "pin": "10001-2345"},
    )
    check("right shapes save, a foreign postcode included", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    r = order(customer_information={"registration": "Unregistered", "gst": "NA", "pan": "NA"})
    check("an unregistered customer's GST and PAN are not checked", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    r = order()
    check("a draft with nothing filled in still saves", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    # ======================================= registered needs a PAN
    banner("2. A registered organisation needs a PAN before it leaves draft")

    r = order(customer_information={"registration": "Registered"})
    check("saved as a draft without one", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    draft = r.json()["data"]

    sent = api("post", "/approvals", owner, json={
        "document_type": "SALES_ORDER", "document_id": draft["id"],
        "document_number": draft.get("order_number"), "price_type": "ECP",
        "discount_percent": 0, "document_value": draft.get("grand_total") or 0,
    })
    check("but not sent for approval", refused(sent, "PAN is required"), f"{sent.status_code} {sent.text[:120]}")

    confirmed = api("put", f"/orders/{draft['id']}/status", owner, json={"status": "CONFIRMED"})
    check("nor confirmed", refused(confirmed, "PAN is required"), f"{confirmed.status_code} {confirmed.text[:120]}")

    fixed = api("put", f"/orders/{draft['id']}", owner, json={
        "customer_information": {"registration": "Registered", "pan": "AAACQ7899Q"},
    })
    check("the PAN is added", fixed.status_code == 200, f"{fixed.status_code} {fixed.text[:120]}")

    sign_off_order(draft, owner)
    confirmed = api("put", f"/orders/{draft['id']}/status", owner, json={"status": "CONFIRMED"})
    check("and now it is confirmed", confirmed.status_code == 200, f"{confirmed.status_code} {confirmed.text[:120]}")

    # ================================================== opportunities
    banner("3. An opportunity refuses the same")

    def opportunity(**fields):
        return api("post", "/opportunities/", owner, json={
            "title": f"Checks {TAG}", "organization_name": f"Checks {TAG}",
            "contact_name": "Ramesh", **fields,
        })

    check("a bad mobile", refused(opportunity(mobile_number="12345"), "digits"))
    check("a bad email", refused(opportunity(email="ramesh.mailinator.com"), "not a valid email"))
    check("a bad GSTIN", refused(opportunity(gst_number="09ABC"), "15"))
    check("a bad PAN", refused(opportunity(pan_number="1234567890"), "five"))
    check("a bad billing PIN", refused(opportunity(zip_code="2013"), "Billing PIN"))
    check("a bad shipping PIN", refused(opportunity(shipping_zip_code="ABCDEF", shipping_country="India"), "Shipping PIN"))

    r = opportunity(country="Malaysia", zip_code="50450-AB", mobile_number="9876543210")
    if r.status_code == 200:
        created_opportunities.append(r.json()["data"]["id"])
    check("a foreign postcode is not held to six digits", "PIN" not in r.text, f"{r.status_code} {r.text[:120]}")

    # ================================================== proposals
    banner("4. A proposal refuses the same")

    def proposal(**fields):
        return api("post", "/quotations/", owner, json={
            "organization_name": f"Checks {TAG}", "contact_name": "Ramesh", **fields,
        })

    check("a bad mobile", refused(proposal(mobile_number="0000"), "digits"))
    check("a bad email", refused(proposal(email="x@y"), "not a valid email"))
    check("a bad billing PIN", refused(proposal(billing_address={"country": "India", "zip_code": "99"}), "Billing PIN"))
    check("a bad shipping PIN", refused(proposal(shipping_address={"zip_code": "1234567"}), "Shipping PIN"))

    r = proposal(billing_address={"country": "China", "zip_code": "100000-X"})
    if r.status_code == 200:
        created_quotations.append(r.json()["data"]["id"])
    check("a foreign postcode is not held to six digits", "PIN" not in r.text, f"{r.status_code} {r.text[:120]}")

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    banner("5. Clearing what the test created")

    try:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()

        if created_orders:
            for sql in (
                "delete from notifications where module = 'sales_order' and entity_id = any(:ids)",
                "delete from sales_approval where document_type = 'SALES_ORDER' and document_id = any(:ids)",
                "delete from sales_order_activity where sales_order_id = any(:ids)",
                "delete from sales_order where id = any(:ids)",
            ):
                session.execute(text(sql), {"ids": created_orders})

        if created_quotations:
            for sql in (
                "delete from notifications where module = 'quotation' and entity_id = any(:ids)",
                "delete from sales_approval where document_type = 'QUOTATION' and document_id = any(:ids)",
                "delete from sales_quotation_activity where quotation_id = any(:ids)",
                "delete from sales_quotation where id = any(:ids)",
            ):
                session.execute(text(sql), {"ids": created_quotations})

        if created_opportunities:
            for sql in (
                "delete from notifications where module = 'opportunity' and entity_id = any(:ids)",
                "delete from sales_opportunity_activity where opportunity_id = any(:ids)",
                "delete from sales_opportunity where id = any(:ids)",
            ):
                session.execute(text(sql), {"ids": created_opportunities})

        session.commit()
        left = session.execute(
            text("select count(*) from sales_order where id = any(:ids)"),
            {"ids": created_orders or [0]},
        ).scalar()
        session.close()

        print(
            f"   removed {len(created_orders)} orders, {len(created_opportunities)} "
            f"opportunities, {len(created_quotations)} proposals"
        )
        print(f"   left behind: {left}")
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
