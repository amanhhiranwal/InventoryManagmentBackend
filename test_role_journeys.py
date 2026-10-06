"""One deal, walked end to end, with every role doing its own part.

The other suites each take a slice - the hierarchy, the discount chain, the
desks, the stock ledger. This one is the whole thing in order, the way it
actually happens on a working day, and it is the suite to read alongside
the manual test script:

    1.  everyone signs in and gets exactly the screens their role allows
    2.  an Area Manager brings in a customer, a lead, an opportunity and a
        quotation at 18% off
    3.  the AVP approves it, then the CEO, and only in that order
    4.  the Area Manager turns it into a sales order and invoices it
    5.  accounts verify the advance - and nobody else can
    6.  inventory pick it, dispatch it, deliver it - and nobody else can
    7.  the salesperson signs off the installation
    8.  accounts settle the balance and close it
    9.  all the way through, the CEO, the AVP and the Zonal Head can see
        where it has got to without being able to push it
    10. every role's bell holds their own work, and the super admin's
        holds everybody's
    11. the catalogue is complete, a short order is flagged as short, and
        dispatch takes every line off the shelf
    12. a record raised in error can be deleted, and one with work built
        on top of it cannot
    13. a deleted document does not hand its reference to the next one

Run seed_sales_team.py, seed_fulfilment_roles.py, seed_role_access.py and
seed_product_catalogue.py first. Everything this creates is removed again,
and the stock it consumes is put back.

    docker exec -w /app backend_app python test_role_journeys.py
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone

from seed_sales_team import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    TEAM_PASSWORD,
    api,
    email_for,
    login,
    rows,
)

ACCOUNTS_EMAIL = "accounts@mailinator.com"
INVENTORY_EMAIL = "inventory@mailinator.com"

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


#: Who signs in, and the sidebar their role should get. Top-level titles in
#: order, with the children that hang off them - which is the same list the
#: manual test script tells a tester to expect, so the two cannot drift.
SIDEBARS = {
    "admin": [
        ("Dashboard", []),
        ("Sales", ["Leads", "Opportunity", "Proposal", "Sales Order", "Proforma Invoice"]),
        ("Users", []),
        ("Inventory", []),
        ("Customers", []),
        ("Accounts", ["Accounts Desk"]),
        ("Procurement", ["Procurement Desk"]),
        ("Reports", []),
        ("Masters", [
            "Companies", "Locations", "Customer Type", "Product Type",
            "Category Group", "Units", "Lead Source", "States",
            "Bank Details", "Roles & Access", "Company Profile",
            "Proposal Approval",
        ]),
        ("Workflows", []),
    ],
    # The sales floor all see the same screens. What separates them is the
    # reporting line - whose records show up inside those screens - not the
    # menu, which is why this list is shared.
    "sales": [
        ("Dashboard", []),
        ("Sales", ["Leads", "Opportunity", "Proposal", "Sales Order", "Proforma Invoice"]),
        ("Users", []),
        ("Inventory", []),
        ("Customers", []),
        ("Reports", []),
    ],
    # A desk gets its desk and nothing else of the sales CRM - not even the
    # dashboard, so the app drops them straight onto the screen they work.
    "accounts": [
        ("Accounts", ["Accounts Desk"]),
    ],
    "inventory": [
        ("Inventory", []),
        ("Procurement", ["Procurement Desk"]),
    ],
}

WHO = [
    ("admin", ADMIN_EMAIL, ADMIN_PASSWORD, "admin"),
    ("ceo", email_for("ceo"), TEAM_PASSWORD, "sales"),
    ("avp", email_for("avp"), TEAM_PASSWORD, "sales"),
    ("zh_north", email_for("zh_north"), TEAM_PASSWORD, "sales"),
    ("zh_south", email_for("zh_south"), TEAM_PASSWORD, "sales"),
    ("am_north_1", email_for("am_north_1"), TEAM_PASSWORD, "sales"),
    ("am_north_2", email_for("am_north_2"), TEAM_PASSWORD, "sales"),
    ("am_south_1", email_for("am_south_1"), TEAM_PASSWORD, "sales"),
    ("accounts", ACCOUNTS_EMAIL, TEAM_PASSWORD, "accounts"),
    ("inventory", INVENTORY_EMAIL, TEAM_PASSWORD, "inventory"),
]

token = {}
created = {
    "customers": [], "leads": [], "opportunities": [], "quotations": [],
    "orders": [], "invoices": [], "approvals": [],
}
#: serial -> what was on the shelf before this suite touched it.
stock_before_run: dict[str, float] = {}


def set_stock(serial: str, quantity: float) -> None:
    """Put a known number on the shelf for a fixture SKU.

    Only ever called on a serial whose opening figure has already been
    recorded in ``stock_before_run``, so the clear-up puts back exactly
    what was there.
    """

    item = next(
        (
            i for i in rows(api("get", "/inventory/items", token["admin"]))
            if str(i.get("serial_number") or "").upper() == serial.upper()
        ),
        None,
    )

    if item is None:
        return

    attributes = dict(item.get("attributes") or {})
    attributes["instock"] = quantity
    attributes["stock"] = quantity

    api("put", f"/inventory/items/{item['_id']}", token["admin"], json={
        "name": item.get("name"),
        "serial_number": item.get("serial_number"),
        "product_type_code": item.get("product_type_code"),
        "category": item.get("category"),
        "attributes": attributes,
        "company_id": item.get("company_id"),
    })


def shape(sidebar) -> list:
    """The sidebar as (title, [child titles]), for comparing against above."""

    return [
        (m["title"], [c["title"] for c in (m.get("children") or [])])
        for m in sidebar
    ]


try:
    # ============================================ 1. everyone signs in
    banner("1. Everyone signs in, and gets the screens their role allows")

    for key, email, password, expected in WHO:
        try:
            token[key] = login(email, password)
        except Exception as exc:  # noqa: BLE001
            check(f"{key} can sign in", False, f"{email}: {exc}")
            continue

        check(f"{key} signs in as {email}", True)

        got = shape(api("get", "/menus/sidebar", token[key]).json()["data"])
        want = SIDEBARS[expected]

        check(
            f"{key} gets the {expected} sidebar",
            got == want,
            f"got {got}",
        )

    admin = token["admin"]
    owner = token["am_north_1"]

    # An accounts person must not be able to open the warehouse's desk, and
    # the warehouse must not be able to open accounts'.
    check(
        "accounts are refused the procurement desk",
        api("get", "/fulfilment/procurement", token["accounts"]).status_code == 403,
    )
    check(
        "the warehouse is refused the accounts desk",
        api("get", "/fulfilment/accounts", token["inventory"]).status_code == 403,
    )
    check(
        "a salesperson is refused both desks",
        api("get", "/fulfilment/accounts", owner).status_code == 403
        and api("get", "/fulfilment/procurement", owner).status_code == 403,
    )

    # ============================================ 2. the Area Manager
    banner("2. The Area Manager brings in the work")

    now = datetime.now(timezone.utc)
    org = f"Journey Farms {TAG}"

    r = api("post", "/customers", owner, json={
        "name": org,
        "contact_name": "Ramesh Gupta",
        "email": f"journey.{TAG}@mailinator.com",
        "phone": "+91 9812345670",
        "city": "Delhi",
        "state": "Delhi",
        "country": "India",
        "lead_source": "Marketing",
        "create_lead": True,
    })
    check("a customer is added, with its lead", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    customer = r.json()["data"]
    created["customers"].append(customer["id"])

    lead_id = customer.get("lead_id")
    check("the lead came with the customer", bool(lead_id))
    created["leads"].append(lead_id)

    for status in ("CONTACTED", "QUALIFIED"):
        r = api("put", f"/leads/{lead_id}/progress", owner, json={"stage": "lead", "status": status})
        check(f"the lead is worked to {status}", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    r = api("post", "/opportunities/", owner, json={
        "lead_id": lead_id,
        "title": f"{org} classrooms",
        "deal_value": 1000000,
        "organization_name": org,
        "contact_name": "Ramesh Gupta",
        "email": f"journey.{TAG}@mailinator.com",
        "mobile_number": "+91 9812345670",
    })
    check("it becomes an opportunity", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    opportunity = r.json()["data"]
    created["opportunities"].append(opportunity["id"])

    r = api("post", "/quotations/", owner, json={
        "opportunity_id": opportunity["id"],
        "organization_name": org,
        "contact_name": "Ramesh Gupta",
        "email": f"journey.{TAG}@mailinator.com",
        "mobile_number": "+91 9812345670",
        "quotation_date": now.isoformat(),
        "validation_date": (now + timedelta(days=30)).isoformat(),
        "items": [{
            "product": "Interactive Flat Panel 75in",
            "model": "Qonevo IFP 75",
            "sku": "SG-IFP-75-SPX-3576",
            "quantity": 5,
            "unit_price": 185000,
            "tax": 18,
        }],
    })
    check("a quotation is raised on it", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    quotation = r.json()["data"]
    created["quotations"].append(quotation["id"])

    value = float(quotation["subtotal"])
    check("the quotation is priced off the catalogue", value == 925000, str(value))

    # ============================================ 3. up the chain
    banner("3. 18% off goes to the AVP, then the CEO")

    r = api("post", "/approvals", owner, json={
        "document_type": "QUOTATION",
        "document_id": quotation["id"],
        "document_number": quotation.get("quotation_number") or f"QT-{TAG}",
        "price_type": "ECP",
        "discount_percent": 18,
        "discount_amount": value * 0.18,
        "document_value": value,
        "remarks": "Bulk classroom order.",
    })
    check("it goes up for approval", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    approval = r.json()["data"]
    created["approvals"].append(approval["id"])

    check("the AVP is asked first", approval["waiting_on"] == "AVP", str(approval["waiting_on"]))
    check(
        "the quotation is held while the chain runs",
        api("get", f"/quotations/{quotation['id']}", owner).json()["data"]["status"] == "PENDING_APPROVAL",
    )

    r = api("put", f"/approvals/{approval['id']}/decide", token["zh_north"], json={"approve": True})
    check("a Zonal Head has no discounting power", r.status_code == 403, f"got {r.status_code}")

    r = api("put", f"/approvals/{approval['id']}/decide", token["ceo"], json={"approve": True})
    check("the CEO cannot jump the AVP's step", r.status_code == 403, f"got {r.status_code}")

    r = api("put", f"/approvals/{approval['id']}/decide", token["avp"], json={
        "approve": True, "remarks": "Fine up to my limit, passing it up.",
    })
    check("the AVP approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    check("and it moves to the CEO", r.json()["data"]["waiting_on"] == "CEO", str(r.json()["data"]["waiting_on"]))

    r = api("put", f"/approvals/{approval['id']}/decide", token["ceo"], json={
        "approve": True, "remarks": "Approved.",
    })
    check("the CEO approves", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    check("the request is cleared", r.json()["data"]["status"] == "APPROVED", str(r.json()["data"]["status"]))
    check(
        "and the quotation is released to be sent",
        api("get", f"/quotations/{quotation['id']}", owner).json()["data"]["status"] == "DRAFT",
    )

    # ============================================ 4. order and invoice
    banner("4. The order is raised and invoiced")

    r = api("post", "/orders", owner, json={
        "customer_name": org,
        "opportunity_id": opportunity["id"],
        "company_name": "Synergy North Agro",
        "state": "Delhi",
        "order_date": now.isoformat(),
        "items": [{
            "product": "Interactive Flat Panel 75in",
            "model": "Qonevo IFP 75",
            "sku": "SG-IFP-75-SPX-3576",
            "qty": 5,
            "rate": 185000,
            "tax_rate": 18,
        }],
    })
    check("the quotation turns into a sales order", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    order = r.json()["data"]
    created["orders"].append(order["id"])

    def order_now():
        return api("get", f"/orders/{order['id']}", admin).json()["data"]

    r = api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})
    check("the salesperson confirms it", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    r = api("post", "/proforma-invoices", owner, json={
        "sales_order_id": order["id"],
        "status": "DRAFT",
        "advance_percent": 30,
    })
    check("a proforma invoice is raised on it", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    invoice = r.json()["data"]
    created["invoices"].append(invoice["id"])

    grand_total = float(invoice["grand_total"])
    advance = round(grand_total * 0.3, 2)

    r = api("put", f"/proforma-invoices/{invoice['id']}", owner, json={"amount_paid": advance})
    check("the advance is recorded on the invoice", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    after = order_now()
    check("the figure reaches the order", abs(float(after["advance_received"]) - advance) < 1, str(after["advance_received"]))
    check(
        "but the order does not move itself - accounts have to say so",
        after["status"] == "CONFIRMED",
        after["status"],
    )

    # ============================================ 5. accounts
    banner("5. Accounts verify the advance, and nobody else can")

    for key in ("am_north_1", "zh_north", "avp", "ceo", "inventory"):
        r = api("put", f"/orders/{order['id']}/status", token[key], json={"status": "PAYMENT_VERIFIED"})
        check(f"{key} cannot verify the payment", r.status_code == 403, f"got {r.status_code} {r.text[:110]}")

    queue = api("get", "/fulfilment/accounts", token["accounts"]).json()["data"]
    mine = next((o for o in queue["orders"] if o["id"] == order["id"]), None)

    check("the order is on the accounts desk", mine is not None)
    check("the desk shows what it is being asked", bool(mine and mine.get("asks")), str(mine and mine.get("asks")))
    check(
        "and that the advance covers what was asked for",
        bool(mine and mine.get("advance_settled")),
        str(mine and (mine.get("advance_received"), mine.get("advance_expected"))),
    )

    r = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": False,
    })
    check("a rejection with no reason is refused", r.status_code >= 400, f"got {r.status_code}")

    r = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Advance seen in the bank.",
    })
    check("accounts verify it", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("the order moves to Payment Verified", order_now()["status"] == "PAYMENT_VERIFIED", order_now()["status"])
    check(
        "and it has left their desk",
        not any(
            o["id"] == order["id"] and o["status"] == "CONFIRMED"
            for o in api("get", "/fulfilment/accounts", token["accounts"]).json()["data"]["orders"]
        ),
    )

    # ============================================ 6. the warehouse
    banner("6. Inventory take it through, and nobody else can")

    queue = api("get", "/fulfilment/procurement", token["inventory"]).json()["data"]
    mine = next((o for o in queue["orders"] if o["id"] == order["id"]), None)

    check("the order lands on the procurement desk", mine is not None)

    lines = (mine or {}).get("stock") or []
    check("the desk reads the shelf for the order's line", len(lines) == 1, str(lines))
    check(
        "it recognises the product from the catalogue",
        bool(lines and lines[0].get("known")),
        str(lines[:1]),
    )
    check(
        "12 in hand covers the 5 ordered",
        bool(lines and lines[0]["wanted"] == 5 and not lines[0]["short"]),
        str(lines[:1]),
    )

    def shelf(serial):
        for row in rows(api("get", "/inventory/items", token["inventory"])):
            if str(row.get("serial_number") or "").upper() == serial.upper():
                return float((row.get("attributes") or {}).get("instock") or 0)
        return None

    stock_before_run["SG-IFP-75-SPX-3576"] = shelf("SG-IFP-75-SPX-3576")
    opening = stock_before_run["SG-IFP-75-SPX-3576"]

    for stage, label in (
        ("PROCUREMENT", "taken into procurement"),
        ("READY", "picked and ready"),
        ("DISPATCHED", "sent out"),
        ("DELIVERED", "delivered"),
    ):
        # Whoever is not the warehouse is refused first.
        r = api("put", f"/orders/{order['id']}/status", token["accounts"], json={"status": stage})
        check(f"accounts cannot mark it {stage}", r.status_code == 403, f"got {r.status_code}")

        r = api("put", f"/fulfilment/orders/{order['id']}/decide", token["inventory"], json={
            "approve": True, "remarks": f"{label.capitalize()}.",
        })
        check(f"the warehouse marks it {stage} - {label}", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
        check(f"the order is at {stage}", order_now()["status"] == stage, order_now()["status"])

        if stage in ("PROCUREMENT", "READY"):
            check(
                f"the shelf is untouched at {stage}",
                shelf("SG-IFP-75-SPX-3576") == opening,
                f"{shelf('SG-SPX7-LANGO3576')} - should still be {opening}",
            )

        if stage == "DISPATCHED":
            check(
                "dispatch takes the 5 off the shelf",
                shelf("SG-IFP-75-SPX-3576") == opening - 5,
                f"{shelf('SG-SPX7-LANGO3576')} - expected {opening - 5}",
            )

    # Read from the order's own detail rather than the desk queue: by now
    # it has been delivered and dropped out of the queue, which is exactly
    # when somebody asks what was issued against it.
    detail = api("get", f"/fulfilment/orders/{order['id']}", token["inventory"]).json()["data"]
    movements = detail.get("stock_movements") or []

    check("the movement is still on the record after delivery", len(movements) == 1, str(movements))
    check(
        "it says what went and what was left",
        bool(movements and movements[0]["quantity"] == 5
             and movements[0]["stock_before"] == opening
             and movements[0]["stock_after"] == opening - 5),
        str(movements[:1]),
    )
    check("and who did it", bool(movements and movements[0].get("actor")), str(movements[:1]))

    # ============================================ 7-8. sign-off and close
    banner("7. The salesperson signs off, accounts close it")

    r = api("put", f"/orders/{order['id']}/status", token["inventory"], json={"status": "INSTALLED"})
    check("the warehouse does not sign off the installation", r.status_code == 403, f"got {r.status_code}")

    r = api("put", f"/orders/{order['id']}/status", owner, json={
        "status": "INSTALLED", "remarks": "Installed and handed over.",
    })
    check("the salesperson signs off the installation", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    r = api("put", f"/proforma-invoices/{invoice['id']}", owner, json={"amount_paid": grand_total})
    check("the balance is settled on the invoice", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    settled = order_now()
    check(
        "nothing is outstanding on the order",
        float(settled.get("outstanding_balance") or 0) < 1,
        str(settled.get("outstanding_balance")),
    )

    r = api("put", f"/orders/{order['id']}/status", owner, json={"status": "COMPLETED"})
    check("the salesperson cannot close it themselves", r.status_code == 403, f"got {r.status_code}")

    r = api("put", f"/fulfilment/orders/{order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Balance received in full.",
    })
    check("accounts close the order", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("it is Completed", order_now()["status"] == "COMPLETED", order_now()["status"])

    # ============================================ 9. who could watch
    banner("8. Who could follow it the whole way")

    history = rows(api("get", f"/orders/{order['id']}/activities", admin))
    stages = {str(entry.get("action") or "") for entry in history}

    check(
        "the order's own history is the trail everyone reads",
        len(history) >= 8,
        f"{len(history)} entries: {sorted(stages)}",
    )

    for key, can_see in (
        ("ceo", True), ("avp", True), ("zh_north", True),
        ("am_north_1", True),
        ("zh_south", False), ("am_north_2", False), ("am_south_1", False),
    ):
        r = api("get", f"/orders/{order['id']}", token[key])
        got = r.status_code == 200
        check(
            f"{key} {'can' if can_see else 'cannot'} follow the order",
            got == can_see,
            f"got {r.status_code}",
        )

        if can_see:
            entries = api("get", f"/orders/{order['id']}/activities", token[key])
            check(f"{key} can read its history", entries.status_code == 200, f"got {entries.status_code}")

    # ============================================ 10. bells
    banner("9. Every bell holds its owner's work")

    def bell(key):
        return api("get", "/notifications", token[key]).json().get("data") or []

    for key in ("ceo", "avp", "zh_north", "am_north_1", "accounts", "inventory"):
        rows_ = bell(key)
        check(
            f"{key}'s bell is their own work only",
            all(not n.get("for_user") for n in rows_),
            str([n.get("for_user") for n in rows_][:4]),
        )

    admin_bell = bell("admin")
    check(
        "the super admin's bell covers the whole business",
        any(n.get("for_user") for n in admin_bell),
        str([n.get("for_user") for n in admin_bell][:4]),
    )
    check(
        "and names whose each row is",
        all(n.get("for_user") for n in admin_bell),
        str([n.get("for_user") for n in admin_bell][:4]),
    )

    # ============================================ 11. the catalogue
    banner("10. The catalogue, and an order it cannot cover")

    catalogue = rows(api("get", "/inventory/items", token["inventory"]))
    by_serial = {
        str(i.get("serial_number") or "").upper(): i
        for i in catalogue
    }

    check(
        "the warehouse sees a stocked catalogue, not three sample rows",
        len(catalogue) >= 20,
        f"{len(catalogue)} products",
    )

    groups = {str(i.get("category") or "") for i in catalogue}
    master_groups = {g["name"] for g in rows(api("get", "/category-groups/", admin))}

    check(
        "every product's Category Group is one that exists in Masters",
        groups and groups <= master_groups,
        f"{sorted(groups - master_groups)} are not category groups",
    )

    units_master = set(api("get", "/inventory/units", admin).json().get("data") or [])
    units_used = {
        str((i.get("attributes") or {}).get("unit") or "")
        for i in catalogue
    } - {""}

    check(
        "and every unit it is priced in is in the Units master",
        units_used <= units_master,
        f"{sorted(units_used - units_master)} are not units",
    )

    # A line of each kind: plenty, thin, and nothing at all.
    PLENTY, THIN, NONE_LEFT = "SG-IFP-65-SPX-EDLA", "SG-IFP-86-SPX-V100", "SG-STD-TOUCH"

    for serial in (PLENTY, THIN, NONE_LEFT):
        check(f"{serial} is on the shelf to order against", serial in by_serial)
        stock_before_run[serial] = shelf(serial)

    # Set the shelf to what each role in this section needs, rather than
    # hoping the catalogue still happens to hold it. These three stand for
    # plenty, thin and nothing, and the whole section is about the desk's
    # shortfall warning - so when a repricing run or somebody's picking
    # changed the counts, the assertions quietly stopped testing anything.
    # Whatever was there is put back by the clear-up, which already records
    # the opening figures above.
    #: serial -> what this section puts on the shelf, and therefore what
    #: the assertions below count against.
    FIXTURE = {PLENTY: 40.0, THIN: 3.0, NONE_LEFT: 0.0}

    for serial, quantity in FIXTURE.items():
        set_stock(serial, quantity)

    r = api("post", "/orders", owner, json={
        "customer_name": f"Short Order {TAG}",
        "company_name": "Synergy North Agro",
        "state": "Delhi",
        "order_date": now.isoformat(),
        "items": [
            # Name, SKU, rate and HSN all off the same catalogue line. They
            # had drifted apart when the SKUs were repointed to the Synergy
            # catalogue and the names left behind, so an order read
            # "Wi-Fi 6 Access Point" against the SKU of a 65" panel - and
            # every document raised from it printed that contradiction.
            {
                "product": '65" Interactive Flat Panel SPX6 (Lango 3576)',
                "model": "Lango RK3576, 8GB RAM / 128GB ROM, Android 16",
                "sku": PLENTY, "hsn": "85285900",
                "qty": 4, "rate": 68000, "tax_rate": 18,
            },
            {
                "product": '86" Interactive Flat Panel CPX8 (LangoV100)',
                "model": "Lango V100, 8GB RAM / 128GB ROM, Android 14",
                "sku": THIN, "hsn": "85285900",
                "qty": 5, "rate": 88000, "tax_rate": 18,
            },
            {
                "product": "Standee Touch",
                "model": "Touch standee cabinet",
                "sku": NONE_LEFT, "hsn": "85285900",
                "qty": 2, "rate": 60000, "tax_rate": 18,
            },
        ],
    })
    check("a three-line order is raised", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    short_order = r.json()["data"]
    created["orders"].append(short_order["id"])

    api("put", f"/orders/{short_order['id']}/status", owner, json={"status": "CONFIRMED"})

    # Accounts will not pass an order with nothing recorded against it, so
    # this one is invoiced and paid like any other before it reaches the
    # warehouse. The money is section 5's subject; here it is only setup.
    short_invoice = api("post", "/proforma-invoices", owner, json={
        "sales_order_id": short_order["id"],
        "status": "DRAFT",
        "advance_percent": 30,
    }).json()["data"]
    created["invoices"].append(short_invoice["id"])

    api("put", f"/proforma-invoices/{short_invoice['id']}", owner, json={
        "amount_paid": round(float(short_invoice["grand_total"]) * 0.3, 2),
    })

    api("put", f"/fulfilment/orders/{short_order['id']}/decide", token["accounts"], json={
        "approve": True, "remarks": "Paid up front.",
    })

    board = api("get", "/fulfilment/procurement", token["inventory"]).json()["data"]
    short = next((o for o in board["orders"] if o["id"] == short_order["id"]), None)
    lines = {
        str(line.get("sku") or "").upper(): line
        for line in ((short or {}).get("stock") or [])
    }

    check("the desk reads all three lines", len(lines) == 3, str(sorted(lines)))
    check(
        f"{PLENTY}: 4 of {FIXTURE[PLENTY]:.0f} is covered",
        bool(lines.get(PLENTY) and not lines[PLENTY]["short"]),
        str(lines.get(PLENTY)),
    )
    check(
        f"{THIN}: 5 wanted against {FIXTURE[THIN]:.0f} is flagged short",
        bool(lines.get(THIN) and lines[THIN]["short"]),
        str(lines.get(THIN)),
    )
    check(
        f"{NONE_LEFT}: nothing in hand is flagged short",
        bool(lines.get(NONE_LEFT) and lines[NONE_LEFT]["short"]),
        str(lines.get(NONE_LEFT)),
    )
    check(
        "and the order as a whole is marked short",
        bool(short and short.get("stock_short")),
        str(short and short.get("stock_short")),
    )

    # The warehouse can still send what it has - being short is a warning,
    # not a lock. What matters is that the shelf ends up telling the truth.
    for _ in range(3):
        api("put", f"/fulfilment/orders/{short_order['id']}/decide", token["inventory"], json={
            "approve": True, "remarks": "Part shipment agreed with the customer.",
        })

    check(
        "it is dispatched",
        api("get", f"/orders/{short_order['id']}", admin).json()["data"]["status"] == "DISPATCHED",
        api("get", f"/orders/{short_order['id']}", admin).json()["data"]["status"],
    )
    check(
        f"{PLENTY} falls by the 4 that went",
        shelf(PLENTY) == FIXTURE[PLENTY] - 4,
        f"{shelf(PLENTY)} from {FIXTURE[PLENTY]}",
    )
    check(
        f"{THIN} is emptied rather than going negative",
        shelf(THIN) == 0,
        str(shelf(THIN)),
    )
    check(
        f"{NONE_LEFT} stays at nothing rather than going negative",
        shelf(NONE_LEFT) == 0,
        str(shelf(NONE_LEFT)),
    )

    # ============================================ 12. company scope
    banner("11. A company's own products stay its own")

    def catalogue_for(key):
        return {
            str(i.get("serial_number") or "").upper()
            for i in rows(api("get", "/inventory/items", token[key]))
        }

    north, south = catalogue_for("am_north_1"), catalogue_for("am_south_1")

    check("the North salesperson sees the North demo kit", "SG-DEMO-NORTH" in north)
    check("and not the South one", "SG-DEMO-SOUTH" not in north, str(sorted(north - south)[:5]))
    check("the South salesperson sees the South demo kit", "SG-DEMO-SOUTH" in south)
    check("and not the North one", "SG-DEMO-NORTH" not in south)
    check(
        "both see the shared catalogue",
        "SG-IFP-75-SPX-3576" in north and "SG-IFP-75-SPX-3576" in south,
    )

    # ============================================ 12. deleting a record
    banner("12. A record can be deleted, until something depends on it")

    r = api("post", "/leads/", owner, json={
        "title": f"Scrap {TAG}",
        "organization_name": f"Scrap {TAG}",
        "contact_name": "Wrong Number",
        "email": f"scrap.{TAG}@mailinator.com",
        "mobile_number": "+91 9812345670",
        "city": "Delhi",
        "country": "India",
    })
    check("a lead is raised in error", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    scrap = r.json()["data"]

    check(
        "another zone cannot delete it",
        api("delete", f"/leads/{scrap['id']}", token["am_south_1"]).status_code == 403,
    )
    check(
        "its own Zonal Head can be trusted with it",
        api("get", f"/leads/{scrap['id']}", token["zh_north"]).status_code == 200,
    )

    removed = api("delete", f"/leads/{scrap['id']}", owner)
    check("the person who raised it can delete it", removed.status_code == 200, f"{removed.status_code} {removed.text[:150]}")
    check(
        "and it is gone",
        api("get", f"/leads/{scrap['id']}", owner).status_code == 404,
    )

    # The journey's own records are still wired together, so they are the
    # ready-made case for the dependency checks.
    blocked = api("delete", f"/leads/{lead_id}", owner)
    check(
        "a lead that became an opportunity is refused",
        blocked.status_code == 409,
        f"got {blocked.status_code} {blocked.text[:120]}",
    )
    check(
        "and the refusal names the opportunity",
        "opportunity" in blocked.text.lower(),
        blocked.text[:120],
    )

    blocked = api("delete", f"/opportunities/{opportunity['id']}", owner)
    check(
        "an opportunity with a quotation on it is refused",
        blocked.status_code == 409,
        f"got {blocked.status_code} {blocked.text[:120]}",
    )

    # An order raised *from* a quotation cites it, and that citation is
    # what the guard looks for.
    r = api("post", "/orders", owner, json={
        "customer_name": org,
        "quotation_id": str(quotation["id"]),
        "company_name": "Synergy North Agro",
        "state": "Delhi",
        "order_date": now.isoformat(),
        "items": [{
            "product": "Interactive Flat Panel 75in",
            "sku": "SG-IFP-75-SPX-3576",
            "qty": 1,
            "rate": 185000,
            "tax_rate": 18,
        }],
    })
    check("an order is raised from the quotation", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    if r.status_code == 200:
        created["orders"].append(r.json()["data"]["id"])

        blocked = api("delete", f"/quotations/{quotation['id']}", owner)
        check(
            "a quotation with an order behind it is refused",
            blocked.status_code == 409,
            f"got {blocked.status_code} {blocked.text[:140]}",
        )

    # ================================ 13. a reference is never handed out twice
    banner("13. A deleted document does not hand its number on")

    def raise_throwaway(label):
        raised = api("post", "/orders", owner, json={
            "customer_name": f"{label} {TAG}",
            "company_name": "Synergy North Agro",
            "state": "Delhi",
            "order_date": now.isoformat(),
            "items": [{
                "product": "Interactive Flat Panel 75in",
                "sku": "SG-IFP-75-SPX-3576",
                "qty": 1,
                "rate": 185000,
                "tax_rate": 18,
            }],
        })

        return raised.json()["data"]

    from sqlalchemy import text as _sql

    from app.database.postgres import SessionLocal as _Session

    first = raise_throwaway("Numbering A")
    check("an order takes the next number", bool(first.get("order_number")), str(first.get("order_number")))

    # Remove it the way a real deletion would, then raise another. The
    # next number used to be worked out as "the highest row plus one", so
    # this second order took the first one's reference - and every stock
    # movement, email and notification naming it meant two different
    # orders.
    scrub = _Session()
    scrub.execute(_sql("delete from sales_order_activity where sales_order_id = :id"), {"id": first["id"]})
    scrub.execute(_sql("delete from notifications where module = 'sales_order' and entity_id = :id"), {"id": first["id"]})
    scrub.execute(_sql("delete from sales_order where id = :id"), {"id": first["id"]})
    scrub.commit()
    scrub.close()

    second = raise_throwaway("Numbering B")
    created["orders"].append(second["id"])

    check(
        "the next one does not reuse it",
        second.get("order_number") != first.get("order_number"),
        f"both came out as {first.get('order_number')}",
    )

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    banner("14. Clearing up, and putting the stock back")

    try:
        admin = token.get("admin") or login(ADMIN_EMAIL, ADMIN_PASSWORD)

        # The shelf first: a test that eats a day's stock is worse than no
        # test at all.
        for serial, was in stock_before_run.items():
            if was is None:
                continue

            item = next(
                (
                    i for i in rows(api("get", "/inventory/items", admin))
                    if str(i.get("serial_number") or "").upper() == serial.upper()
                ),
                None,
            )

            if item is None:
                continue

            attributes = dict(item.get("attributes") or {})

            if float(attributes.get("instock") or 0) == was:
                continue

            attributes["instock"] = was

            api("put", f"/inventory/items/{item['_id']}", admin, json={
                "name": item.get("name"),
                "serial_number": item.get("serial_number"),
                "product_type_code": item.get("product_type_code"),
                "category": item.get("category"),
                "attributes": attributes,
                "company_id": item.get("company_id"),
            })
            print(f"   {serial} put back to {was:.0f}")

        from sqlalchemy import text

        from app.database.mongodb import sync_mongo_db
        from app.database.postgres import SessionLocal

        orders = [int(i) for i in created["orders"] if i]

        if orders:
            sync_mongo_db["inventory_movements"].delete_many({"order_id": {"$in": orders}})

        session = SessionLocal()

        def run(sql, **params):
            try:
                session.execute(text(sql), params)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                print("   could not clear:", str(exc).split("\n")[0][:90])

        for invoice_id in created["invoices"]:
            api("delete", f"/proforma-invoices/{invoice_id}", admin)

        if created["approvals"]:
            # The chain's steps live in a JSON column on the row itself,
            # so there is nothing else hanging off it to clear.
            run("delete from sales_approval where id = any(:ids)", ids=created["approvals"])

        if orders:
            # Order lines are a JSON column on sales_order, not a table.
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=orders)
            run("delete from sales_order where id = any(:ids)", ids=orders)

        # Committed before the API deletes below, which run on their own
        # connection: left open, this transaction's deleted orders are
        # still there as far as they are concerned, and every dependency
        # check refuses.
        session.commit()

        # Through the API, in the order the dependency checks require:
        # a quotation blocks its opportunity, and an opportunity blocks the
        # lead it came from. Doing it this way also means a suite run
        # exercises the delete endpoints rather than reaching past them
        # into the tables.
        for group in ("quotations", "opportunities", "leads", "customers"):
            path = {
                "quotations": "/quotations",
                "opportunities": "/opportunities",
                "leads": "/leads",
                "customers": "/customers",
            }[group]

            for record_id in created[group]:
                if record_id is None:
                    continue

                removed = api("delete", f"{path}/{record_id}", admin)

                if removed.status_code >= 400:
                    print(
                        f"   could not remove {group[:-1]} {record_id}: "
                        f"{removed.status_code} {removed.text[:90]}"
                    )

        session.commit()
        session.close()

        print(
            f"   removed {len(orders)} orders, "
            f"{len(created['quotations'])} quotations, "
            f"{len(created['customers'])} customers and what hung off them"
        )
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
