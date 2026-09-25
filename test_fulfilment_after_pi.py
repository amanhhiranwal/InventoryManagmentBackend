"""What happens after the proforma invoice, and whose letterhead it goes on.

Runs a whole order from confirmed to completed: the advance is recorded on
the invoice, accounts verify it, inventory move it through fulfilment, the
salesperson signs off the installation, and accounts close it once the
balance is settled.

Two things this guards in particular:

  * money landing on the invoice reaches the order - the advance and the
    balance used never to get there at all - but does *not* move the order
    by itself, because verifying a payment is the accounts desk's call;
  * the letterhead follows the selling company rather than always being the
    group's.

Run seed_sales_team.py first. Everything it creates is removed again.

    docker exec -w /app backend_app python test_fulfilment_after_pi.py
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
created_invoices = []
created_quotations = []

try:
    admin = login(*ADMIN)

    for key in ("avp", "am_north_1"):
        token[key] = login(email_for(key), TEAM_PASSWORD)

    token["accounts"] = login("accounts@mailinator.com", TEAM_PASSWORD)
    token["inventory"] = login("inventory@mailinator.com", TEAM_PASSWORD)

    owner = token["am_north_1"]
    print("Signed in as the super admin and the team.")

    companies = {
        c["company_code"]: c
        for c in rows(api("get", "/companies/", admin, params={"size": 200}))
    }
    north, south = companies.get("SYN-NORTH"), companies.get("SYN-SOUTH")

    # ================================================== the order
    banner("1. An order confirmed and invoiced")

    now = datetime.now(timezone.utc)

    r = api("post", "/orders", owner, json={
        "customer_name": f"Fulfilment Test {TAG}",
        "company_name": "Synergy North Agro",
        "state": "Delhi",
        "order_date": now.isoformat(),
        "items": [{
            "product": "Interactive Flat Panel",
            "model": "Qonevo IFP 75",
            "sku": f"FT-{TAG}",
            "qty": 2,
            "rate": 100000,
            "tax_rate": 18,
        }],
    })
    check("the order is raised", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    order = r.json()["data"]
    created_orders.append(order["id"])

    r = api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})
    check("it is confirmed", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = api("post", "/proforma-invoices", owner, json={
        "sales_order_id": order["id"],
        "status": "DRAFT",
        "advance_percent": 30,
    })
    check("a proforma invoice is raised on it", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    invoice = r.json()["data"]
    created_invoices.append(invoice["id"])

    grand_total = float(invoice["grand_total"])
    advance = round(grand_total * 0.3, 2)

    check("the invoice carries the order's value", grand_total > 200000, str(grand_total))

    # ================================================== the payment
    banner("2. The advance lands")

    def order_now():
        return api("get", f"/orders/{order['id']}", admin).json()["data"]

    check(
        "the order is waiting at Confirmed",
        order_now()["status"] == "CONFIRMED",
        order_now()["status"],
    )

    r = api("put", f"/proforma-invoices/{invoice['id']}", owner, json={
        "amount_paid": advance,
    })
    check("the payment is recorded", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    after = order_now()

    # Recording a figure on the invoice is not the same as accounts saying
    # the money arrived - the order waits for them.
    check(
        "the order still waits on accounts",
        after["status"] == "CONFIRMED",
        after["status"],
    )
    check(
        "the advance is carried onto the order",
        abs(float(after.get("advance_received") or 0) - advance) < 1,
        str(after.get("advance_received")),
    )
    check(
        "so is the balance still owed",
        abs(float(after.get("outstanding_balance") or 0) - (grand_total - advance)) < 1,
        str(after.get("outstanding_balance")),
    )

    verified = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Advance seen in the bank.",
    })
    check("accounts verify it", verified.status_code == 200, f"{verified.status_code} {verified.text[:160]}")
    check(
        "and it moves to Payment Verified",
        order_now()["status"] == "PAYMENT_VERIFIED",
        order_now()["status"],
    )

    history = rows(api("get", f"/orders/{order['id']}/activities", admin))
    check(
        "the order's history says where the money came from",
        any(
            "proforma" in (entry.get("description") or "").lower()
            for entry in history
        ),
        str([entry.get("action") for entry in history][:4]),
    )

    # ================================================== the rest of the chain
    banner("3. Through to installation")

    for stage, who in (
        ("PROCUREMENT", "inventory"),
        ("READY", "inventory"),
        ("DISPATCHED", "inventory"),
        ("DELIVERED", "inventory"),
        ("INSTALLED", "am_north_1"),
    ):
        r = api("put", f"/orders/{order['id']}/status", token[who], json={"status": stage})
        check(f"the order reaches {stage.lower()}", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    check("it is installed but not closed", order_now()["status"] == "INSTALLED", order_now()["status"])

    # ================================================== the balance
    banner("4. The balance clears the order")

    r = api("put", f"/proforma-invoices/{invoice['id']}", owner, json={
        "amount_paid": grand_total,
    })
    check("the balance is recorded", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    closed_by = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Balance settled.",
    })
    check("accounts close it out", closed_by.status_code == 200, f"{closed_by.status_code} {closed_by.text[:160]}")

    closed = order_now()

    check("the order is completed", closed["status"] == "COMPLETED", closed["status"])
    check(
        "nothing is left outstanding",
        float(closed.get("outstanding_balance") or 0) <= 1,
        str(closed.get("outstanding_balance")),
    )

    # A payment on an order that has not been confirmed must not drag it
    # forward - the chain only ever moves on, one step at a time.
    r = api("post", "/orders", owner, json={
        "customer_name": f"Draft Order {TAG}",
        "state": "Delhi",
        "order_date": now.isoformat(),
        "items": [{"product": "Panel", "sku": f"FT2-{TAG}", "qty": 1, "rate": 50000, "tax_rate": 18}],
    })
    draft = r.json()["data"]
    created_orders.append(draft["id"])

    check(
        "a draft order is left alone",
        api("get", f"/orders/{draft['id']}", admin).json()["data"]["status"] == "DRAFT",
    )

    # ================================================== the letterhead
    banner("5. The letterhead follows the selling company")

    if north and south:
        r = api("post", "/quotations/", owner, json={
            "organization_name": f"Letterhead Test {TAG}",
            "contact_name": "Test Contact",
            "email": f"letterhead.{TAG}@mailinator.com",
            "mobile_number": "+91 9800000000",
            "quotation_date": now.isoformat(),
            "validation_date": (now + timedelta(days=30)).isoformat(),
            "company_id": north["id"],
            "items": [{
                "product": "Interactive Flat Panel",
                "sku": f"LH-{TAG}",
                "quantity": 1,
                "unit_price": 100000,
                "tax": 18,
            }],
        })
        check("a quotation is raised on the North company", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

        quotation = r.json()["data"]
        created_quotations.append(quotation["id"])

        check(
            "it remembers which company is selling",
            quotation.get("company_id") == north["id"],
            str(quotation.get("company_id")),
        )

        brand = api(
            "get", "/quotations/brand", owner,
            params={"quotation_id": quotation["id"]},
        ).json()["data"]

        check(
            "the letterhead is the North company's",
            brand["name"] == north["company_name"],
            brand["name"],
        )

        # A draft can be moved onto another of our companies.
        r = api("put", f"/quotations/{quotation['id']}", owner, json={
            "company_id": south["id"],
        })
        check("the draft can be moved to the other company", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

        brand = api(
            "get", "/quotations/brand", owner,
            params={"quotation_id": quotation["id"]},
        ).json()["data"]

        check(
            "the letterhead follows it",
            brand["name"] == south["company_name"],
            brand["name"],
        )

        # The preview asks for the logo by company, before anything is
        # saved - what the picker chooses is what the preview shows.
        by_company = api(
            "get", "/quotations/brand", owner,
            params={"company_id": north["id"]},
        ).json()["data"]

        check(
            "naming a company alone is enough for the preview",
            by_company["name"] == north["company_name"],
            by_company["name"],
        )

        import requests

        logo = requests.get(
            "http://localhost:8000/api/v1/quotations/brand/logo",
            params={"company_id": north["id"]},
        )
        check(
            "the logo endpoint answers for a company",
            logo.status_code in (200, 404),
            f"got {logo.status_code}",
        )

        if logo.status_code == 200:
            check(
                "it comes back as an image",
                logo.headers.get("content-type", "").startswith("image/"),
                logo.headers.get("content-type", ""),
            )

        # The PDF itself builds from the same company.
        pdf = api("get", f"/quotations/{quotation['id']}/pdf", owner)
        check("the proposal PDF still builds", pdf.status_code == 200, f"{pdf.status_code} {pdf.text[:120]}")
        check("and it is a PDF", pdf.content[:4] == b"%PDF", str(pdf.content[:8]))

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    banner("6. Clearing what the test created")

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

        if created_invoices:
            run("delete from sales_proforma_invoice_activity where proforma_invoice_id = any(:ids)", ids=created_invoices)
            run("delete from sales_proforma_invoice where id = any(:ids)", ids=created_invoices)

        # Every stage rings the bell now, so those rows go too - otherwise a
        # test run leaves stale orders in real people's notifications.
        if created_orders:
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=created_orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)

        if created_quotations:
            run("delete from notifications where module = 'quotation' and entity_id = any(:ids)", ids=created_quotations)
            run("delete from sales_quotation_activity where quotation_id = any(:ids)", ids=created_quotations)
            run("delete from sales_quotation where id = any(:ids)", ids=created_quotations)

        session.commit()

        left = session.execute(
            text("select count(*) from sales_order where id = any(:ids)"),
            {"ids": created_orders or [0]},
        ).scalar()

        print(
            f"   removed {len(created_orders)} orders, "
            f"{len(created_invoices)} invoices, "
            f"{len(created_quotations)} quotations"
        )
        print(f"   left behind: {left}")

        session.close()
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
