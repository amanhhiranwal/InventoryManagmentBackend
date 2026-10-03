"""Seed demo CRM data covering the whole Lead -> Opportunity -> Sales Order flow.

Creates leads at every canonical status, opportunities at every pipeline
stage, and sales orders at every order status, so each screen, board column,
status tab and transition can be exercised without hand-entering records.

Every row is tagged with SEED_TAG in its remarks, so the data can be removed
again cleanly:

    python seed_crm_demo_data.py            # create (skips if already seeded)
    python seed_crm_demo_data.py --reset    # delete existing seed rows, recreate
    python seed_crm_demo_data.py --clear    # delete seed rows and stop
    python seed_crm_demo_data.py --reset --fresh   # leads only, all at NEW

Only rows carrying the tag are ever deleted; anything you create yourself is
left alone.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.workflow_status import (  # noqa: E402
    LeadStatus,
    OpportunityStatus,
    SalesOrderStatus,
)
from app.database.postgres import SessionLocal  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.models.sales_order import SalesOrder  # noqa: E402

SEED_TAG = "[demo-seed]"

NOW = datetime.utcnow()


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def days_ahead(n: int) -> datetime:
    return NOW + timedelta(days=n)


# ---------------------------------------------------------------------------
# Source data
# ---------------------------------------------------------------------------
# Master data is named here and resolved to ids at run time. It used to be
# numbered, which assumed the masters were exactly as first seeded: by now
# customer type 4 had been deleted and two of the states pointed at the
# wrong row, so Chennai was filed under Andhra Pradesh and Hyderabad too.
# A name that no longer exists stops the run and says which, rather than
# writing a record with a dangling reference.
#
# (contact, organisation, email, mobile, designation, city, state, pin,
#  customer_type, lead_source, status, days_old)
LEADS = [
    ("Rajesh Kumar", "Infosys BPM", "rajesh.kumar@infosysbpm.example",
     "9845012301", "Facilities Head", "Bengaluru", "Karnataka", "560100",
     "Corporate", "Marketing", LeadStatus.NEW, 2),
    ("Priya Sharma", "Wipro Enterprises", "priya.sharma@wipro.example",
     "9845012302", "Procurement Manager", "Bengaluru", "Karnataka", "560035",
     "Corporate", "In-bound", LeadStatus.NEW, 4),
    ("Arun Menon", "Tata Elxsi", "arun.menon@tataelxsi.example",
     "9845012303", "IT Director", "Thiruvananthapuram", "Kerala", "695581",
     "OEM", "Marketing", LeadStatus.CONTACTED, 7),
    ("Sneha Patil", "Godrej Interio", "sneha.patil@godrej.example",
     "9845012304", "Showroom Head", "Mumbai", "Maharashtra", "400079",
     "Distributor", "Cold Calling", LeadStatus.CONTACTED, 9),
    ("Vikram Desai", "Reliance Retail", "vikram.desai@relianceretail.example",
     "9845012305", "Store Operations Lead", "Ahmedabad", "Gujarat", "380015",
     "Distributor", "In-bound", LeadStatus.QUALIFIED, 12),
    ("Anita Rao", "HDFC Bank", "anita.rao@hdfcbank.example",
     "9845012306", "Branch Infrastructure Manager", "Mumbai", "Maharashtra",
     "400051", "Corporate", "Marketing", LeadStatus.QUALIFIED, 14),
    ("Manish Gupta", "Brightline Interiors", "manish.gupta@brightline.example",
     "9845012307", "Founder", "New Delhi", "Delhi", "110024",
     "End Customer", "Cold Calling", LeadStatus.LOST, 20),
]

# Opportunities, each converted from its own lead.
# (contact, org, email, mobile, designation, city, state, pin,
#  customer_type, status, deal_value, priority, close_in_days, days_old)
OPPORTUNITIES = [
    ("Deepak Nair", "Zoho Corporation", "deepak.nair@zoho.example",
     "9845012311", "Workplace Lead", "Chennai", "Tamil Nadu", "600113",
     "Corporate", OpportunityStatus.QUALIFICATION, 450000, "Medium", 45, 10),
    ("Kavita Iyer", "Titan Company", "kavita.iyer@titan.example",
     "9845012312", "Retail Design Head", "Bengaluru", "Karnataka", "560048",
     "Distributor", OpportunityStatus.REQUIREMENT, 780000, "High", 38, 16),
    ("Sandeep Joshi", "Mahindra Logistics", "sandeep.joshi@mahindra.example",
     "9845012313", "Operations Manager", "Pune", "Maharashtra", "411014",
     "OEM", OpportunityStatus.DEMO, 1250000, "High", 30, 21),
    ("Neha Bansal", "Apollo Hospitals", "neha.bansal@apollo.example",
     "9845012314", "Facilities Director", "Hyderabad", "Telangana", "500033",
     "End Customer", OpportunityStatus.PROPOSAL, 2100000, "High", 24, 28),
    ("Rohit Malhotra", "DLF Cyber City", "rohit.malhotra@dlf.example",
     "9845012315", "Property Manager", "Gurugram", "Haryana", "122002",
     "Corporate", OpportunityStatus.NEGOTIATION, 3400000, "High", 15, 35),
    ("Sunita Reddy", "Manipal University", "sunita.reddy@manipal.example",
     "9845012316", "Dean of Infrastructure", "Manipal", "Karnataka", "576104",
     "End Customer", OpportunityStatus.WON, 1850000, "Medium", -5, 48),
    ("Aakash Verma", "Verma Traders", "aakash.verma@vermatraders.example",
     "9845012317", "Proprietor", "Ludhiana", "Punjab", "141001",
     "Distributor", OpportunityStatus.LOST, 320000, "Low", -2, 40),
]

#: Real catalogue lines, with everything a document needs to print.
#:
#: The demo used to carry a name and a rate and nothing else - no SKU, no
#: HSN, no model - so every proposal and invoice raised off it printed a
#: dash where the code belongs and fell back to a flat rate, and the
#: warehouse could not match a line to anything on the shelf. These are
#: lines from the Synergy catalogue, at the rates on the stock dashboard.
#:
#: (product, model, sku, hsn, rate)
PRODUCT_CATALOGUE = [
    (
        '75" Interactive Flat Panel SPX7 (LangoV100)',
        "Lango V100, 8GB RAM / 128GB ROM, Android 14",
        "SG-SPX7-LANGOV100", "84714190", 75000,
    ),
    (
        '86" Interactive Flat Panel CPX8 (LangoV100)',
        "Lango V100, 8GB RAM / 128GB ROM, Android 14, with camera and array mic",
        "SG-CPX8-LANGOV100", "85285900", 88000,
    ),
    (
        "OPS i7 8GB/512GB 11th Gen",
        "Intel i7, 8GB RAM / 512GB SSD, DOS",
        "SG-OPS-I7-8-512-G11", "85291029", 40000,
    ),
    (
        "Standee Touch",
        "Touch standee cabinet",
        "SG-STD-TOUCH", "85285900", 60000,
    ),
]

# (customer, org, customer_type, state, status, qty_per_line, days_old)
# (customer, org, customer_type, state, city, pin, status, qty_per_line,
#  days_old)
#
# One order is billed inside Uttar Pradesh, where we are registered, so the
# demo shows a CGST + SGST invoice beside the IGST ones rather than only
# one of the two paths.
ORDERS = [
    ("Deepak Nair", "Zoho Corporation", "Corporate", "Tamil Nadu",
     "Chennai", "600113", SalesOrderStatus.DRAFT, 2, 6),
    ("Kavita Iyer", "Titan Company", "Distributor", "Karnataka",
     "Bengaluru", "560048", SalesOrderStatus.CONFIRMED, 3, 11),
    ("Sandeep Joshi", "Mahindra Logistics", "OEM", "Uttar Pradesh",
     "Noida", "201301", SalesOrderStatus.ON_HOLD, 4, 15),
    ("Neha Bansal", "Apollo Hospitals", "End Customer", "Telangana",
     "Hyderabad", "500033", SalesOrderStatus.RELEASED, 5, 19),
    ("Sunita Reddy", "Manipal University", "End Customer", "Karnataka",
     "Manipal", "576104", SalesOrderStatus.COMPLETED, 6, 30),
    ("Aakash Verma", "Verma Traders", "Distributor", "Punjab",
     "Ludhiana", "141001", SalesOrderStatus.CANCELLED, 1, 26),
]


def build_items(qty: int):
    """Lines with every field a document prints already filled.

    ``qty`` is carried in both ``qty`` and ``quantity_case``: the documents
    read the first and the older order screens read the second, and a line
    that fills only one shows a quantity on one screen and a blank on the
    next.
    """

    items = []

    for product, model, sku, hsn, rate in PRODUCT_CATALOGUE[: max(1, qty % 4 + 1)]:
        line = rate * qty * 0.95
        tax = round(line * 0.18, 2)

        items.append(
            {
                "product_id": sku,
                "product": product,
                "model": model,
                "description": model,
                "sku": sku,
                "hsn": hsn,
                "rate": rate,
                "price": rate,
                "qty": qty,
                "quantity_case": qty,
                "quantity_kg_ltr": 0,
                "discount": 5,
                "tax_rate": 18,
                "tax_amount": tax,
                "line_total": round(line + tax, 2),
            }
        )

    return items


def totals(items):
    subtotal = sum(i["rate"] * i["qty"] for i in items)
    discount = round(subtotal * 0.05, 2)
    gst = round((subtotal - discount) * 0.18, 2)
    return subtotal, discount, gst, round(subtotal - discount + gst, 2)


def gst_state_code(state: str) -> str:
    """The two digits a GSTIN in this state opens with.

    Read from the same table the tax split is decided by, so a seeded
    registration and the tax charged on it cannot tell different stories.
    """

    from app.core.gst import state_code

    return state_code(state) or "09"


class Masters:
    """Master-list ids, looked up by name.

    Built once per run from whatever is actually in the database. A name
    the masters do not hold raises rather than returning None, because a
    record written with a dangling reference fails later, somewhere else,
    with a message about a foreign key instead of about the seed.
    """

    def __init__(self, db):
        from app.models.customer_type import CustomerType
        from app.models.lead_source import LeadSource
        from app.models.state import State

        def index(model, attr="name"):
            return {
                str(getattr(row, attr)).strip().lower(): row.id
                for row in db.query(model).all()
            }

        self.states = index(State)
        self.customer_types = index(CustomerType)
        self.lead_sources = index(LeadSource)

    @staticmethod
    def _pick(table: dict, name: str, what: str):
        key = str(name or "").strip().lower()

        if key not in table:
            raise SystemExit(
                f"  no {what} called {name!r}.\n"
                f"  The masters hold: {', '.join(sorted(table)) or '(none)'}\n"
                f"  Add it under Masters, or correct the name in this seed."
            )

        return table[key]

    def state(self, name):
        return self._pick(self.states, name, "state")

    def customer_type(self, name):
        return self._pick(self.customer_types, name, "customer type")

    def lead_source(self, name):
        return self._pick(self.lead_sources, name, "lead source")


def clear_seed(db) -> int:
    """Remove only rows this script created."""

    like = f"%{SEED_TAG}%"

    orders = db.execute(
        text("DELETE FROM sales_order WHERE remarks LIKE :t"), {"t": like}
    ).rowcount
    opportunities = db.execute(
        text("DELETE FROM sales_opportunity WHERE remarks LIKE :t"), {"t": like}
    ).rowcount
    leads = db.execute(
        text("DELETE FROM sales_lead WHERE remarks LIKE :t"), {"t": like}
    ).rowcount
    db.commit()

    total = orders + opportunities + leads
    print(
        f"  cleared seed rows: leads={leads} "
        f"opportunities={opportunities} orders={orders}"
    )
    return total


def main():
    reset = "--reset" in sys.argv
    clear_only = "--clear" in sys.argv
    fresh = "--fresh" in sys.argv

    db = SessionLocal()

    try:
        owner = db.execute(
            text("SELECT id FROM users ORDER BY is_super_admin DESC, email LIMIT 1")
        ).scalar()

        if owner is None:
            print("No users exist. Run check_and_seed_db.py first.")
            return

        # Resolved once, against whatever the masters actually hold.
        masters = Masters(db)

        if clear_only or reset:
            print("--- Clearing existing demo seed ---")
            clear_seed(db)
            if clear_only:
                print("Done.")
                return

        already = db.execute(
            text("SELECT count(*) FROM sales_lead WHERE remarks LIKE :t"),
            {"t": f"%{SEED_TAG}%"},
        ).scalar()

        if already:
            print(
                f"Demo data already present ({already} seeded leads). "
                "Use --reset to recreate, or --clear to remove."
            )
            return

        # ---------------- Leads ----------------
        print("--- Creating leads ---")

        # --fresh puts every lead at NEW so the status progression can be
        # driven by hand from the very start of the workflow.
        lead_rows = (
            [(*row[:10], LeadStatus.NEW, row[11]) for row in LEADS]
            if fresh
            else LEADS
        )

        for (
            contact, org, email, mobile, designation, city, state, pin,
            ctype, source, status, age,
        ) in lead_rows:
            state_id = masters.state(state)
            ctype_id = masters.customer_type(ctype)
            source_id = masters.lead_source(source)

            lead = Lead(
                title=f"{org} - display requirement",
                description=f"Inbound enquiry from {org}.",
                status=status,
                stage="dead" if status == LeadStatus.LOST else "lead",
                demo_status="none",
                contact_name=contact,
                organization_name=org,
                email=email,
                mobile_number=mobile,
                website=f"www.{org.split()[0].lower()}.example",
                designation=designation,
                office_address=f"{age + 10} Business Park Road",
                city=city,
                zip_code=pin,
                country="India",
                gst_number=f"29ABCDE{1000 + age}F1Z5",
                pan_number=f"ABCDE{1000 + age}F",
                coi_number=f"U72900KA20{age:02d}PTC0{age:03d}",
                remarks=f"Requires a showroom-grade display setup. {SEED_TAG}",
                requirements="Interactive panels and signage for main floor.",
                customer_type_id=ctype_id,
                state_id=state_id,
                lead_source_id=source_id,
                creator_id=owner,
                created_at=days_ago(age),
                updated_at=days_ago(max(0, age - 1)),
            )
            db.add(lead)
            print(f"  lead: {org:24} {status}")

        db.commit()

        if fresh:
            print("--- Summary (fresh mode: leads only, all at NEW) ---")
            for row in db.execute(
                text("SELECT status, count(*) FROM sales_lead GROUP BY status")
            ):
                print(f"    {row[0]:16} {row[1]}")
            print(
                "\nNo opportunities or orders created - convert the leads "
                "yourself to walk the flow."
            )
            return

        # ---------------- Opportunities (each from its own lead) -------------
        print("--- Creating opportunities (with originating leads) ---")
        opportunity_by_org = {}

        for (
            contact, org, email, mobile, designation, city, state, pin,
            ctype, status, deal, priority, close_in, age,
        ) in OPPORTUNITIES:
            state_id = masters.state(state)
            ctype_id = masters.customer_type(ctype)

            lead = Lead(
                title=f"{org} - display requirement",
                description=f"Converted enquiry from {org}.",
                status=LeadStatus.CONVERTED,
                stage="opportunity",
                demo_status="given" if status != OpportunityStatus.QUALIFICATION else "none",
                contact_name=contact,
                organization_name=org,
                email=email,
                mobile_number=mobile,
                website=f"www.{org.split()[0].lower()}.example",
                designation=designation,
                office_address=f"{age + 4} Corporate Avenue",
                city=city,
                zip_code=pin,
                country="India",
                gst_number=f"27ABCDE{2000 + age}F1Z5",
                pan_number=f"ABCDE{2000 + age}F",
                coi_number=f"U72900MH20{age:02d}PTC0{age:03d}",
                remarks=f"Converted to opportunity. {SEED_TAG}",
                requirements="Multi-site rollout across branches.",
                customer_type_id=ctype_id,
                state_id=state_id,
                lead_source_id=masters.lead_source("Marketing"),
                creator_id=owner,
                created_at=days_ago(age + 6),
                updated_at=days_ago(age),
            )
            db.add(lead)
            db.flush()

            opportunity = Opportunity(
                lead_id=lead.id,
                title=f"{org} - display rollout",
                description=f"Opportunity converted from the {org} enquiry.",
                status=status,
                deal_value=deal,
                priority=priority,
                expected_closing_date=days_ahead(close_in),
                contact_name=contact,
                organization_name=org,
                email=email,
                mobile_number=mobile,
                website=f"www.{org.split()[0].lower()}.example",
                designation=designation,
                office_address=f"{age + 4} Corporate Avenue",
                city=city,
                zip_code=pin,
                country="India",
                gst_number=f"27ABCDE{2000 + age}F1Z5",
                pan_number=f"ABCDE{2000 + age}F",
                coi_number=f"U72900MH20{age:02d}PTC0{age:03d}",
                requirements="Multi-site rollout across branches.",
                remarks=f"Pipeline opportunity. {SEED_TAG}",
                demo_status="given" if status != OpportunityStatus.QUALIFICATION else "none",
                # The opportunity's own lines, carrying the SKU and the
                # HSN so the proposal raised off it starts with both rather
                # than asking somebody to type them in again.
                product_items=[
                    {
                        "name": product,
                        "product": product,
                        "model": model,
                        "sku": sku,
                        "hsn": hsn,
                        "qty": 2,
                        "price": rate,
                        "unitPrice": rate,
                    }
                    for product, model, sku, hsn, rate in PRODUCT_CATALOGUE[:2]
                ],
                customer_type_id=ctype_id,
                state_id=state_id,
                creator_id=owner,
                won_at=days_ago(3) if status == OpportunityStatus.WON else None,
                won_by=owner if status == OpportunityStatus.WON else None,
                won_reason=(
                    "Best total cost of ownership and fastest install window."
                    if status == OpportunityStatus.WON
                    else None
                ),
                lost_reason=(
                    "Budget deferred to next financial year."
                    if status == OpportunityStatus.LOST
                    else None
                ),
                created_at=days_ago(age),
                updated_at=days_ago(max(0, age - 2)),
            )
            db.add(opportunity)
            db.flush()

            opportunity_by_org[org] = opportunity.id
            print(f"  opportunity: {org:24} {status:14} deal={deal:,}")

        db.commit()

        # ---------------- Sales orders ----------------
        print("--- Creating sales orders ---")
        seq = db.execute(text("SELECT coalesce(max(id), 0) FROM sales_order")).scalar()

        for customer, org, ctype, state, city, pin, status, qty, age in ORDERS:
            items = build_items(qty)
            subtotal, discount, gst, grand = totals(items)
            seq += 1

            # A GSTIN opens with the state's own two digits. Seeding every
            # customer with "27" put a Maharashtra registration on a Punjab
            # address, which is the first thing anyone checking a tax
            # document would notice.
            gstin = f"{gst_state_code(state)}ABCDE{3000 + age}F1Z5"

            order = SalesOrder(
                order_number=f"SO-{seq:05d}",
                opportunity_id=opportunity_by_org.get(org),
                status=status,
                customer_name=customer,
                company_name=org,
                customer_type=ctype,
                state=state,
                order_date=days_ago(age),
                assigned_to="Sales Team",
                sales_executive="Super Admin",
                customer_information={
                    "customer_name": customer,
                    "organization_name": org,
                    "customer_type": ctype,
                    "gst": gstin,
                    "pan": f"ABCDE{3000 + age}F",
                    "primary_contact": {
                        "name": customer,
                        "designation": "Procurement",
                        "phone": f"98450123{age:02d}",
                        "email": f"{customer.split()[0].lower()}@{org.split()[0].lower()}.example",
                    },
                },
                billing_address={
                    "street": f"{age + 12} Industrial Estate",
                    "city": city,
                    "state": state,
                    "country": "India",
                    "pin": pin,
                },
                shipping_address={
                    "street": f"{age + 12} Warehouse Lane",
                    "city": city,
                    "state": state,
                    "country": "India",
                    "pin": pin,
                },
                items=items,
                total_amount=subtotal,
                discount_amount=discount,
                gst_amount=gst,
                grand_total=grand,
                aging_0_30=grand if age <= 30 else 0,
                aging_31_60=grand if 30 < age <= 60 else 0,
                remarks=f"Demo order for {org}. {SEED_TAG}",
                creator_id=owner,
                creator_name="Super Admin",
                created_at=days_ago(age),
                updated_at=days_ago(max(0, age - 1)),
            )
            db.add(order)
            print(
                f"  order: SO-{seq:05d} {org:24} {status:10} "
                f"grand={grand:,.0f}"
            )

        db.commit()

        # ---------------- Summary ----------------
        print("--- Summary ---")
        for label, sql in [
            ("leads by status",
             "SELECT status, count(*) FROM sales_lead GROUP BY status ORDER BY 1"),
            ("opportunities by status",
             "SELECT status, count(*) FROM sales_opportunity GROUP BY status ORDER BY 1"),
            ("orders by status",
             "SELECT status, count(*) FROM sales_order GROUP BY status ORDER BY 1"),
        ]:
            print(f"  {label}:")
            for row in db.execute(text(sql)):
                print(f"    {row[0]:16} {row[1]}")

        print("\nDone. Re-run with --clear to remove this data.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
