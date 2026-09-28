"""Remove the records a test run leaves behind.

The suites each try to clear up after themselves with DELETE calls, but
leads, opportunities and quotations have no DELETE endpoint - the API
answers 405 and the call is quietly ignored - so every run added another
"Journey Farms a37534" to the Leads page until the demo data was mostly
test litter.

This clears them properly, straight from the database, matching on the
names the suites generate and nothing else. Seeded demo records
(Greenfield Farms, Riverside Agro, Coastal Seeds) are left alone: the
match is on the tagged names, which always carry a random suffix.

    docker exec -w /app backend_app python clear_test_records.py
    docker exec -w /app backend_app python clear_test_records.py --dry-run
"""

import sys

from sqlalchemy import text

from app.database.mongodb import sync_mongo_db
from app.database.postgres import SessionLocal

#: The customer names the suites raise their records under. Each is always
#: followed by a random tag, so a real record of the same name is safe as
#: long as it does not carry one.
PREFIXES = [
    "Journey Farms ",
    "Stock Test ",
    "Short Order ",
    "Fulfilment Test ",
    "Greenfield Farms ",
    "Banded Discount ",
    "Rejected Discount ",
    "Approval Test ",
    "Desk Test ",
]

#: table, the column holding the name, and what hangs off it.
TARGETS = [
    ("sales_order", "customer_name", [
        ("sales_order_activity", "sales_order_id"),
        ("sales_proforma_invoice", "sales_order_id"),
    ]),
    ("sales_proforma_invoice", "customer_name", [
        ("sales_proforma_invoice_activity", "proforma_invoice_id"),
    ]),
    ("sales_quotation", "organization_name", [
        ("sales_quotation_activity", "quotation_id"),
    ]),
    ("sales_opportunity", "organization_name", [
        ("sales_opportunity_activity", "opportunity_id"),
    ]),
    ("sales_lead", "organization_name", [
        ("sales_lead_activity", "lead_id"),
    ]),
]

#: Customers live in MongoDB, not Postgres, so they are cleared separately.
MONGO_TARGETS = [("customers", "name"), ("customer_activities", "customer_name")]


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    session = SessionLocal()
    clause = " or ".join(f"{{column}} like :p{i}" for i in range(len(PREFIXES)))
    params = {f"p{i}": f"{prefix}%" for i, prefix in enumerate(PREFIXES)}

    total = 0

    try:
        for table, column, children in TARGETS:
            where = clause.format(column=column)

            try:
                ids = [
                    row[0]
                    for row in session.execute(
                        text(f"select id from {table} where {where}"), params
                    )
                ]
            except Exception as exc:  # noqa: BLE001 - a missing table is fine
                session.rollback()
                print(f"{table}: {str(exc).split(chr(10))[0][:90]}")
                continue

            if not ids:
                print(f"{table}: nothing to clear")
                continue

            print(f"{table}: {len(ids)} to clear")

            if dry_run:
                continue

            if table == "sales_order":
                sync_mongo_db["inventory_movements"].delete_many(
                    {"order_id": {"$in": [int(i) for i in ids]}}
                )
                session.execute(
                    text(
                        "delete from notifications "
                        "where module = 'sales_order' and entity_id = any(:ids)"
                    ),
                    {"ids": ids},
                )

            for child, key in children:
                try:
                    session.execute(
                        text(f"delete from {child} where {key} = any(:ids)"),
                        {"ids": ids},
                    )
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    print(f"  {child}: {str(exc).split(chr(10))[0][:80]}")

            session.execute(
                text(f"delete from {table} where id = any(:ids)"), {"ids": ids}
            )

            total += len(ids)

        # ------------------------------------------------------- MongoDB
        pattern = "^(" + "|".join(
            prefix.replace(" ", r"\s") for prefix in PREFIXES
        ) + ")"

        for collection, field in MONGO_TARGETS:
            found = sync_mongo_db[collection].count_documents(
                {field: {"$regex": pattern}}
            )

            if not found:
                print(f"{collection}: nothing to clear")
                continue

            print(f"{collection}: {found} to clear")

            if not dry_run:
                sync_mongo_db[collection].delete_many(
                    {field: {"$regex": pattern}}
                )
                total += found

        if not dry_run:
            session.commit()
            print(f"\n{total} records cleared")
        else:
            print("\nnothing changed (--dry-run)")
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
