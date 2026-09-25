"""Clear the CRM back to an empty, working install.

Removes every transaction - leads, opportunities, quotations, sales
orders, proforma invoices, approvals, notifications, customers and
products - and every user except the super admin, who is kept so there is
still a way in.

Kept: the roles, the hierarchy chart, the company profile, and the masters
(companies, customer types, states, product types). Seed the team again
afterwards with seed_sales_team.py.

    docker exec -w /app backend_app python reset_demo_data.py --yes
"""

import sys

from sqlalchemy import text

from app.database.mongodb import sync_mongo_db
from app.database.postgres import SessionLocal

#: Emptied completely, children before parents.
TRANSACTION_TABLES = [
    "sales_approval",
    "sales_proforma_invoice_activity",
    "sales_proforma_invoice",
    "sales_order_activity",
    "sales_order",
    "sales_quotation_activity",
    "sales_quotation",
    "sales_opportunity_activity",
    "sales_opportunity",
    "sales_lead_activity",
    "sales_lead",
    "notifications",
]

#: Mongo collections holding the same kind of record.
MONGO_COLLECTIONS = [
    "customers",
    "customer_activities",
    "inventory_items",
    "inventory_templates",
    "sales_orders",
    "counters",
]

#: Company rows left over from an earlier audit run.
COMPANY_PATTERN = "^Audit Company [0-9]+$"


def reset(db) -> None:
    print("Clearing transactions")

    for table in TRANSACTION_TABLES:
        try:
            count = db.execute(text(f"select count(*) from {table}")).scalar()
            db.execute(text(f"delete from {table}"))
            print(f"  {table:<34} {count} removed")
        except Exception as exc:
            db.rollback()
            print(f"  {table:<34} skipped: {str(exc).split(chr(10))[0][:70]}")

    db.commit()

    print("\nClearing customers and products")

    for name in MONGO_COLLECTIONS:
        try:
            count = sync_mongo_db[name].count_documents({})
            sync_mongo_db[name].delete_many({})
            print(f"  {name:<34} {count} removed")
        except Exception as exc:
            print(f"  {name:<34} skipped: {exc}")

    print("\nClearing users, keeping the super admin")

    doomed = [
        str(row[0])
        for row in db.execute(
            text("select id from users where is_super_admin is not true")
        )
    ]

    if doomed:
        # Anyone reporting to a user being removed is detached first, or the
        # foreign key refuses the delete.
        db.execute(
            text("update users set reports_to_id = null where reports_to_id = any(cast(:ids as uuid[]))"),
            {"ids": doomed},
        )
        for table in ("user_roles", "user_companies"):
            db.execute(
                text(f"delete from {table} where user_id = any(cast(:ids as uuid[]))"),
                {"ids": doomed},
            )
        db.execute(
            text("delete from users where id = any(cast(:ids as uuid[]))"),
            {"ids": doomed},
        )
        db.commit()

    print(f"  {len(doomed)} users removed")

    print("\nClearing companies left over from the audit run")

    removed = db.execute(
        text("delete from companies where company_name ~ :pattern"),
        {"pattern": COMPANY_PATTERN},
    ).rowcount
    db.commit()
    print(f"  {removed} removed")

    print("\nWhat is left")

    for table in (
        "users", "roles", "companies", "workflows", "app_setting",
        "sales_customer_type", "sales_state", "product_types",
    ):
        try:
            print(f"  {table:<34} {db.execute(text('select count(*) from ' + table)).scalar()}")
        except Exception:
            db.rollback()

    for row in db.execute(text("select email from users order by email")):
        print(f"    kept: {row[0]}")


if __name__ == "__main__":
    if "--yes" not in sys.argv:
        print(__doc__)
        print("Refusing to run without --yes.")
        sys.exit(1)

    session = SessionLocal()

    try:
        reset(session)
    finally:
        session.close()
