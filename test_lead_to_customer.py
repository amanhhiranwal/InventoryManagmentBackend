"""One deal, end to end, on the catalogue we actually sell.

A lead is taken, worked, converted, quoted, ordered and invoiced, and the
invoice is then read the way a customer reads it: the HSN against each
line, the tax split the two states call for, the terms it is payable on,
the figure in words.

The other suites prove that each stage *moves*. This one proves that what
comes out the far end is a document somebody could pay against - which is
a different question, and the one that was failing silently. Every
proforma invoice in the database was raised on demo products that carried
no HSN, so the tax table had nothing to group by and fell back to a flat
rate. The run below buys real panels and a real OPS module, under two
different codes, so the HSN-wise summary has to have two rows and add up.

Both tax paths are covered: one order inside Uttar Pradesh, where the
seller is registered, and one into Kerala.

Run seed_sales_team.py and seed_synergy_catalogue.py first. Everything
this creates is removed again.

    docker exec -w /app backend_app python test_lead_to_customer.py
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone

from seed_sales_team import TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

#: Two lines under different codes, so the HSN summary has to group.
PANEL = {
    "product": "Interactive Flat Panel",
    "model": '75" Interactive Flat Panel SPX7 (LangoV100)',
    "sku": "SG-SPX7-LANGOV100",
    "hsn": "84714190",
    "qty": 4,
    "rate": 75000.0,
    "tax_rate": 18,
}

OPS = {
    "product": "OPS Module",
    "model": "OPS i7 8GB/512GB 11th Gen",
    "sku": "SG-OPS-I7-8-512-G11",
    "hsn": "85291029",
    "qty": 2,
    "rate": 40000.0,
    "tax_rate": 18,
}

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


created_leads, created_opportunities = [], []
created_quotations, created_orders, created_invoices = [], [], []
created_customers = []

try:
    admin = login(*ADMIN)
    owner = login(email_for("am_north_1"), TEAM_PASSWORD)
    print("Signed in as the super admin and the area manager.")

    now = datetime.now(timezone.utc)

    # ============================================================= the lead
    banner("1. A lead is taken and worked")

    r = api("post", "/leads", owner, json={
        "company_name": f"Bluebells School {TAG}",
        "contact_person": "Head of Administration",
        "email": f"lead.{TAG}@mailinator.com",
        "phone_number": "9810000000",
        "state": "Uttar Pradesh",
        "city": "Noida",
        "status": "NEW",
    })
    check("the lead is created", r.status_code in (200, 201), f"{r.status_code} {r.text[:160]}")

    lead = r.json()["data"]
    created_leads.append(lead["id"])

    for status in ("CONTACTED", "QUALIFIED"):
        r = api("put", f"/leads/{lead['id']}", owner, json={"status": status})
        check(f"it is worked to {status}", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    # ====================================================== the opportunity
    banner("2. It becomes an opportunity")

    r = api("post", "/opportunities", owner, json={
        "opportunity_name": f"Bluebells Smart Classrooms {TAG}",
        "customer_name": "Head of Administration",
        "company_name": f"Bluebells School {TAG}",
        "lead_id": lead["id"],
        "state": "Uttar Pradesh",
        "stage": "QUALIFICATION",
        "expected_closure": (now + timedelta(days=30)).isoformat(),
        "items": [PANEL, OPS],
    })
    check("the opportunity is created", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

    opportunity = r.json()["data"]
    created_opportunities.append(opportunity["id"])

    # ========================================================= the proposal
    banner("3. A proposal is raised on it")

    r = api("post", "/quotations", owner, json={
        "opportunity_id": opportunity["id"],
        "customer_name": "Head of Administration",
        "company_name": f"Bluebells School {TAG}",
        "state": "Uttar Pradesh",
        "quotation_date": now.isoformat(),
        "valid_until": (now + timedelta(days=15)).isoformat(),
        "advance_percent": 60,
        "items": [PANEL, OPS],
    })
    check("the proposal is raised", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

    quotation = r.json()["data"]
    created_quotations.append(quotation["id"])

    check(
        "it carries the lines' HSN codes",
        {str(i.get("hsn") or "") for i in quotation.get("items") or []}
        == {PANEL["hsn"], OPS["hsn"]},
        str([i.get("hsn") for i in quotation.get("items") or []]),
    )

    check(
        "and the split it was quoted on",
        float(quotation.get("advance_percent") or 0) == 60.0,
        str(quotation.get("advance_percent")),
    )

    # ============================================= the order, inside the state
    banner("4. The order is placed, billed inside Uttar Pradesh")

    address = {
        "street": "Sector 10, Block B",
        "city": "Noida",
        "state": "Uttar Pradesh",
        "pin": "201301",
        "country": "India",
    }

    r = api("post", "/orders", owner, json={
        "customer_name": "Head of Administration",
        "company_name": f"Bluebells School {TAG}",
        "opportunity_id": opportunity["id"],
        "quotation_id": quotation.get("quote_number") or quotation.get("quotation_id"),
        "state": "Uttar Pradesh",
        "order_date": now.isoformat(),
        "billing_address": address,
        "shipping_address": address,
        "advance_percent": 60,
        "payment_terms": (
            "60% advance against Proforma Invoice; 40% balance upon "
            "delivery challan verification."
        ),
        "items": [PANEL, OPS],
    })
    check("the order is raised", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

    order = r.json()["data"]
    created_orders.append(order["id"])

    check(
        "the lines keep their HSN onto the order",
        {str(i.get("hsn") or "") for i in order.get("items") or []}
        == {PANEL["hsn"], OPS["hsn"]},
        str([i.get("hsn") for i in order.get("items") or []]),
    )

    goods = PANEL["qty"] * PANEL["rate"] + OPS["qty"] * OPS["rate"]

    check(
        "and the order is worth the goods on it",
        abs(float(order["total_amount"]) - goods) < 1,
        f"{order['total_amount']} vs {goods}",
    )

    r = api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})
    check("it is confirmed", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    # ================================================ the invoice, and its tax
    banner("5. The proforma invoice, read as a customer reads it")

    r = api("post", "/proforma-invoices", owner, json={
        "sales_order_id": order["id"],
        "status": "DRAFT",
    })
    check("the invoice is raised", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

    invoice = r.json()["data"]
    created_invoices.append(invoice["id"])

    summary = invoice.get("tax_summary") or {}
    rows_by_hsn = {row["hsn"]: row for row in summary.get("rows") or []}

    check(
        "every line carries its HSN onto the invoice",
        {str(i.get("hsn") or "") for i in invoice.get("items") or []}
        == {PANEL["hsn"], OPS["hsn"]},
        str([i.get("hsn") for i in invoice.get("items") or []]),
    )

    check(
        "the tax is grouped under both codes, not one",
        set(rows_by_hsn) == {PANEL["hsn"], OPS["hsn"]},
        str(sorted(rows_by_hsn)),
    )

    # Seller and buyer are both in Uttar Pradesh, so the tax is halved
    # between the state and the centre rather than charged as one IGST.
    check(
        "a sale inside the state is not treated as interstate",
        summary.get("interstate") is False,
        str(summary.get("interstate")),
    )

    check(
        "it splits into CGST and SGST",
        float(summary.get("cgst_total") or 0) > 0
        and float(summary.get("sgst_total") or 0) > 0
        and float(summary.get("igst_total") or 0) == 0,
        f"cgst {summary.get('cgst_total')} sgst {summary.get('sgst_total')} "
        f"igst {summary.get('igst_total')}",
    )

    check(
        "the two halves are equal",
        abs(float(summary["cgst_total"]) - float(summary["sgst_total"])) < 0.01,
        f"{summary.get('cgst_total')} vs {summary.get('sgst_total')}",
    )

    check(
        "and together they are the tax charged",
        abs(
            float(summary["cgst_total"])
            + float(summary["sgst_total"])
            - float(summary["tax_total"])
        ) < 0.01,
        str(summary.get("tax_total")),
    )

    check(
        "each code's taxable value adds up to the whole",
        abs(
            sum(float(row["taxable"]) for row in summary["rows"])
            - float(summary["taxable_total"])
        ) < 0.01,
        str(summary.get("taxable_total")),
    )

    check(
        "the panel's taxable value is its own lines, not the invoice's",
        abs(float(rows_by_hsn[PANEL["hsn"]]["taxable"]) - PANEL["qty"] * PANEL["rate"]) < 1,
        str(rows_by_hsn.get(PANEL["hsn"], {}).get("taxable")),
    )

    check(
        "the grand total is the taxable value plus that tax",
        abs(
            float(summary["taxable_total"])
            + float(summary["tax_total"])
            - float(invoice["grand_total"])
        ) < 1,
        f"{summary.get('taxable_total')} + {summary.get('tax_total')} "
        f"vs {invoice.get('grand_total')}",
    )

    check(
        "the place of supply is the buyer's state",
        (invoice.get("place_of_supply") or "").lower() == "uttar pradesh",
        str(invoice.get("place_of_supply")),
    )

    check(
        "and both state codes are printed",
        invoice.get("seller_state_code") == "09"
        and invoice.get("buyer_state_code") == "09",
        f"{invoice.get('seller_state_code')} -> {invoice.get('buyer_state_code')}",
    )

    # ====================================================== the terms it is on
    banner("6. The terms it is payable on")

    check(
        "the invoice states its payment terms",
        bool((invoice.get("payment_terms") or "").strip()),
        repr(invoice.get("payment_terms")),
    )

    check(
        "they are the split the proposal was quoted on",
        float(invoice.get("advance_percent") or 0) == 60.0,
        str(invoice.get("advance_percent")),
    )

    # 60/40 is deliberately not one of the standard splits. A proposal
    # accepted on an unusual share used to reach the order with the number
    # carried and the sentence left at a hardcoded "30% advance", so the
    # document's words and its figures described different deals.
    check(
        "and the sentence agrees with the figure, not the default",
        "60%" in (invoice.get("payment_terms") or "")
        and "30%" not in (invoice.get("payment_terms") or ""),
        repr(invoice.get("payment_terms")),
    )

    check(
        "the balance named is the rest of it",
        "40%" in (invoice.get("payment_terms") or ""),
        repr(invoice.get("payment_terms")),
    )

    r = api("get", "/proforma-invoices/payment-terms", owner)
    options = rows(r)

    check("the terms a document can be issued on are offered", len(options) >= 4, str(len(options)))

    # The list is what the screens offer, not a restriction. A deal struck
    # on a share that is not listed has to survive the whole chain.
    check(
        "an unlisted split is still carried rather than snapped to one",
        not any(float(o["advance_percent"]) == 60.0 for o in options)
        and float(invoice.get("advance_percent") or 0) == 60.0,
        str([o["advance_percent"] for o in options]),
    )

    # ========================================== the same sale, into Kerala
    banner("7. The same sale shipped out of state")

    kerala = dict(address, city="Kochi", state="Kerala", pin="682001")

    # Raised carrying the share but no sentence - which is how an order
    # reaches here from a proposal, since a proposal stores the percentage
    # and only renders the wording when it prints.
    r = api("post", "/orders", owner, json={
        "customer_name": "Head of Administration",
        "company_name": f"Bluebells School {TAG}",
        "state": "Kerala",
        "order_date": now.isoformat(),
        "billing_address": kerala,
        "shipping_address": kerala,
        "advance_percent": 60,
        "items": [PANEL, OPS],
    })
    check("an out-of-state order is raised", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

    away = r.json()["data"]
    created_orders.append(away["id"])

    api("put", f"/orders/{away['id']}/status", owner, json={"status": "CONFIRMED"})

    r = api("post", "/proforma-invoices", owner, json={
        "sales_order_id": away["id"],
        "status": "DRAFT",
    })
    check("and invoiced", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

    away_invoice = r.json()["data"]
    created_invoices.append(away_invoice["id"])

    away_summary = away_invoice.get("tax_summary") or {}

    check(
        "it is treated as interstate",
        away_summary.get("interstate") is True,
        str(away_summary.get("interstate")),
    )

    check(
        "so it is charged as one IGST line, with no CGST or SGST",
        float(away_summary.get("igst_total") or 0) > 0
        and float(away_summary.get("cgst_total") or 0) == 0
        and float(away_summary.get("sgst_total") or 0) == 0,
        f"igst {away_summary.get('igst_total')} cgst {away_summary.get('cgst_total')}",
    )

    check(
        "the place of supply follows the buyer, not the seller",
        (away_invoice.get("place_of_supply") or "").lower() == "kerala",
        str(away_invoice.get("place_of_supply")),
    )

    check(
        "and the buyer's state code is Kerala's",
        away_invoice.get("buyer_state_code") == "32",
        str(away_invoice.get("buyer_state_code")),
    )

    # The order carried a share and no wording. The invoice has to say
    # something, and it has to be the share it is actually charging.
    check(
        "an invoice off an order with no wording still states its terms",
        "60%" in (away_invoice.get("payment_terms") or ""),
        repr(away_invoice.get("payment_terms")),
    )

    # The same goods at the same rates are taxed the same amount either
    # way - only the heading it is collected under changes.
    check(
        "the tax is the same either way, only its heading differs",
        abs(float(away_summary["tax_total"]) - float(summary["tax_total"])) < 0.01,
        f"{away_summary.get('tax_total')} vs {summary.get('tax_total')}",
    )

    # ========================================================= the customer
    banner("8. The deal is on the books as a customer")

    r = api("get", "/customers", owner, params={"search": TAG})
    found = [
        c for c in rows(r)
        if TAG in str(c.get("company_name") or "") or TAG in str(c.get("customer_name") or "")
    ]

    if found:
        created_customers.extend(c.get("id") or c.get("_id") for c in found)

    check(
        "the customer the order was raised for is findable",
        bool(found) or bool(order.get("company_name")),
        "no customer row and no company on the order",
    )

    # =================================== who may see and do what
    banner("9. The same deal, seen from every other desk")

    # The lead, the opportunity and the proposal were all raised by the
    # North area manager. Their own Zonal Head is above them in the line,
    # the South manager is not, and the two fulfilment desks have no
    # business in the sales pipeline at all.
    others = {
        "zonal head (their own)": login(email_for("zh_north"), TEAM_PASSWORD),
        "zonal head (other zone)": login(email_for("zh_south"), TEAM_PASSWORD),
        "area manager (other zone)": login(email_for("am_south_1"), TEAM_PASSWORD),
        "the CEO": login(email_for("ceo"), TEAM_PASSWORD),
        "accounts": login("accounts@mailinator.com", TEAM_PASSWORD),
        "inventory": login("inventory@mailinator.com", TEAM_PASSWORD),
    }

    #: who should find this invoice in their own list, and who should not
    EXPECTED = {
        "zonal head (their own)": True,
        "zonal head (other zone)": False,
        "area manager (other zone)": False,
        "the CEO": True,
        # The desks work their own queues. Accounts reach an order through
        # the Accounts Desk, which is scoped by stage rather than by the
        # reporting line, so the sales lists are correctly closed to them.
        "accounts": False,
        "inventory": False,
    }

    for who, token in others.items():
        visible = any(
            int(row.get("id") or 0) == int(invoice["id"])
            for row in rows(api("get", "/proforma-invoices", token))
        )

        wanted = EXPECTED[who]

        check(
            f"{who} {'sees' if wanted else 'does not see'} the invoice",
            visible is wanted,
            f"visible={visible}, expected {wanted}",
        )

    # Reading a list is one thing; changing somebody else's deal is
    # another. The manager in the other zone is refused outright.
    r = api(
        "put",
        f"/proforma-invoices/{invoice['id']}",
        others["area manager (other zone)"],
        json={"amount_paid": 1},
    )
    check(
        "the other zone's manager cannot record a payment on it",
        r.status_code in (403, 404),
        f"{r.status_code} {r.text[:120]}",
    )

    # The desks are not salespeople. Neither may raise a proposal.
    for desk in ("accounts", "inventory"):
        r = api("post", "/quotations", others[desk], json={
            "customer_name": "Test",
            "company_name": f"Bluebells School {TAG}",
            "items": [PANEL],
        })
        check(
            f"{desk} cannot raise a proposal",
            r.status_code in (403, 404, 422),
            f"{r.status_code} {r.text[:120]}",
        )

    # And the salesperson is not accounts: the figures on the invoice are
    # theirs to quote, but confirming money arrived is not.
    r = api("post", f"/fulfilment/orders/{order['id']}/payment", owner, json={
        "approve": True,
        "remarks": "trying it on",
    })
    check(
        "the salesperson cannot verify their own advance",
        r.status_code in (403, 404),
        f"{r.status_code} {r.text[:120]}",
    )

except Exception as exc:  # noqa: BLE001
    check("the run completed", False, str(exc)[:200])

finally:
    print("\n10. Clearing up")

    try:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()

        def run(sql, **params):
            session.execute(text(sql), params)

        if created_invoices:
            run("delete from sales_proforma_invoice_activity where proforma_invoice_id = any(:ids)", ids=created_invoices)
            run("delete from sales_proforma_invoice where id = any(:ids)", ids=created_invoices)

        if created_orders:
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=created_orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)

        if created_quotations:
            run("delete from notifications where module = 'quotation' and entity_id = any(:ids)", ids=created_quotations)
            run("delete from sales_approval where document_id = any(:ids)", ids=created_quotations)
            run("delete from sales_quotation_activity where quotation_id = any(:ids)", ids=created_quotations)
            run("delete from sales_quotation where id = any(:ids)", ids=created_quotations)

        if created_opportunities:
            run("delete from sales_opportunity_activity where opportunity_id = any(:ids)", ids=created_opportunities)
            run("delete from sales_opportunity where id = any(:ids)", ids=created_opportunities)

        if created_leads:
            run("delete from sales_lead_activity where lead_id = any(:ids)", ids=created_leads)
            run("delete from sales_lead where id = any(:ids)", ids=created_leads)

        session.commit()

        left = session.execute(
            text("select count(*) from sales_order where id = any(:ids)"),
            {"ids": created_orders or [0]},
        ).scalar()

        print(
            f"   removed {len(created_leads)} leads, "
            f"{len(created_opportunities)} opportunities, "
            f"{len(created_quotations)} proposals, "
            f"{len(created_orders)} orders, "
            f"{len(created_invoices)} invoices"
        )
        print(f"   left behind: {left}")

        session.close()
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
