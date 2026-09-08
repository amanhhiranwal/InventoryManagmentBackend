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
# (contact, organisation, email, mobile, designation, city, state_id, pin,
#  customer_type_id, lead_source_id, status, days_old)
LEADS = [
    ("Rajesh Kumar", "Infosys BPM", "rajesh.kumar@infosysbpm.example",
     "9845012301", "Facilities Head", "Bengaluru", 5, "560100", 5, 1,
     LeadStatus.NEW, 2),
    ("Priya Sharma", "Wipro Enterprises", "priya.sharma@wipro.example",
     "9845012302", "Procurement Manager", "Bengaluru", 5, "560035", 5, 3,
     LeadStatus.NEW, 4),
    ("Arun Menon", "Tata Elxsi", "arun.menon@tataelxsi.example",
     "9845012303", "IT Director", "Thiruvananthapuram", 6, "695581", 2, 1,
     LeadStatus.CONTACTED, 7),
    ("Sneha Patil", "Godrej Interio", "sneha.patil@godrej.example",
     "9845012304", "Showroom Head", "Mumbai", 7, "400079", 1, 2,
     LeadStatus.CONTACTED, 9),
    ("Vikram Desai", "Reliance Retail", "vikram.desai@relianceretail.example",
     "9845012305", "Store Operations Lead", "Ahmedabad", 3, "380015", 1, 3,
     LeadStatus.QUALIFIED, 12),
    ("Anita Rao", "HDFC Bank", "anita.rao@hdfcbank.example",
     "9845012306", "Branch Infrastructure Manager", "Mumbai", 7, "400051", 5, 1,
     LeadStatus.QUALIFIED, 14),
    ("Manish Gupta", "Brightline Interiors", "manish.gupta@brightline.example",
     "9845012307", "Founder", "New Delhi", 2, "110024", 3, 2,
     LeadStatus.LOST, 20),
]

# Opportunities, each converted from its own lead.
# (contact, org, email, mobile, designation, city, state_id, pin,
#  customer_type_id, status, deal_value, priority, close_in_days, days_old)
OPPORTUNITIES = [
    ("Deepak Nair", "Zoho Corporation", "deepak.nair@zoho.example",
     "9845012311", "Workplace Lead", "Chennai", 1, "600113", 5,
     OpportunityStatus.QUALIFICATION, 450000, "Medium", 45, 10),
    ("Kavita Iyer", "Titan Company", "kavita.iyer@titan.example",
     "9845012312", "Retail Design Head", "Bengaluru", 5, "560048", 1,
     OpportunityStatus.REQUIREMENT, 780000, "High", 38, 16),
    ("Sandeep Joshi", "Mahindra Logistics", "sandeep.joshi@mahindra.example",
     "9845012313", "Operations Manager", "Pune", 7, "411014", 2,
     OpportunityStatus.DEMO, 1250000, "High", 30, 21),
    ("Neha Bansal", "Apollo Hospitals", "neha.bansal@apollo.example",
     "9845012314", "Facilities Director", "Hyderabad", 1, "500033", 4,
     OpportunityStatus.PROPOSAL, 2100000, "High", 24, 28),
    ("Rohit Malhotra", "DLF Cyber City", "rohit.malhotra@dlf.example",
     "9845012315", "Property Manager", "Gurugram", 4, "122002", 5,
     OpportunityStatus.NEGOTIATION, 3400000, "High", 15, 35),
    ("Sunita Reddy", "Manipal University", "sunita.reddy@manipal.example",
     "9845012316", "Dean of Infrastructure", "Manipal", 5, "576104", 4,
     OpportunityStatus.WON, 1850000, "Medium", -5, 48),
    ("Aakash Verma", "Verma Traders", "aakash.verma@vermatraders.example",
     "9845012317", "Proprietor", "Ludhiana", 8, "141001", 1,
     OpportunityStatus.LOST, 320000, "Low", -2, 40),
]

PRODUCT_CATALOGUE = [
    ("Interactive Flat Panel", 185000),
    ("LED Video Wall", 420000),
    ("Digital Signage", 96000),
    ("Commercial Display", 68000),
]

# (customer, org, customer_type, state, status, qty_per_line, days_old)
ORDERS = [
    ("Deepak Nair", "Zoho Corporation", "Corporate", "Tamil Nadu",
     SalesOrderStatus.DRAFT, 2, 6),
    ("Kavita Iyer", "Titan Company", "Distributor", "Karnataka",
     SalesOrderStatus.CONFIRMED, 3, 11),
    ("Sandeep Joshi", "Mahindra Logistics", "OEM", "Maharashtra",
     SalesOrderStatus.ON_HOLD, 4, 15),
    ("Neha Bansal", "Apollo Hospitals", "Institution", "Telangana",
     SalesOrderStatus.RELEASED, 5, 19),
    ("Sunita Reddy", "Manipal University", "Institution", "Karnataka",
     SalesOrderStatus.COMPLETED, 6, 30),
    ("Aakash Verma", "Verma Traders", "Distributor", "Punjab",
     SalesOrderStatus.CANCELLED, 1, 26),
]


def build_items(qty: int):
    items = []
    for name, rate in PRODUCT_CATALOGUE[: max(1, qty % 4 + 1)]:
        line = rate * qty
        tax = round(line * 0.18, 2)
        items.append(
            {
                "product_id": name.lower().replace(" ", "-"),
                "description": name,
                "rate": rate,
                "price": rate,
                "quantity_case": qty,
                "quantity_kg_ltr": 0,
                "discount": 5,
                "tax_rate": 18,
                "tax_amount": tax,
                "line_total": line + tax,
            }
        )
    return items


def totals(items):
    subtotal = sum(i["rate"] * i["quantity_case"] for i in items)
    discount = round(subtotal * 0.05, 2)
    gst = round((subtotal - discount) * 0.18, 2)
    return subtotal, discount, gst, round(subtotal - discount + gst, 2)


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
            contact, org, email, mobile, designation, city, state_id, pin,
            ctype_id, source_id, status, age,
        ) in lead_rows:
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
            contact, org, email, mobile, designation, city, state_id, pin,
            ctype_id, status, deal, priority, close_in, age,
        ) in OPPORTUNITIES:
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
                lead_source_id=1,
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
                product_items=[
                    {"name": n, "qty": 2, "price": p}
                    for n, p in PRODUCT_CATALOGUE[:2]
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

        for customer, org, ctype, state, status, qty, age in ORDERS:
            items = build_items(qty)
            subtotal, discount, gst, grand = totals(items)
            seq += 1

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
                    "gst": f"27ABCDE{3000 + age}F1Z5",
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
                    "city": state,
                    "state": state,
                    "country": "India",
                    "pin": f"5600{age:02d}",
                },
                shipping_address={
                    "street": f"{age + 12} Warehouse Lane",
                    "city": state,
                    "state": state,
                    "country": "India",
                    "pin": f"5600{age:02d}",
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
