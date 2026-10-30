"""Find bell notifications whose record no longer exists, and remove them.

A notification points at its record by module and id. Deleting a record
through the app now takes its notifications with it; a test that deletes
rows directly, or a record removed before that was true, leaves them
behind - an entry in somebody's bell that opens a page for nothing.

Reports by default. Pass --apply to delete what it found.

    docker exec -w /app backend_app python clear_orphan_notifications.py
    docker exec -w /app backend_app python clear_orphan_notifications.py --apply
"""

import sys

from sqlalchemy import text

from app.database.postgres import SessionLocal

#: notification module -> the table its entity_id points into.
RECORD_TABLES = {
    "lead": "sales_lead",
    "opportunity": "sales_opportunity",
    "quotation": "sales_quotation",
    "sales_order": "sales_order",
    "proforma_invoice": "sales_proforma_invoice",
}


def orphans_query(table: str) -> str:
    return (
        "from notifications n where n.module = :module and not exists "
        f"(select 1 from {table} r where r.id = n.entity_id)"
    )


def main(apply: bool) -> int:
    session = SessionLocal()
    total = 0

    try:
        for module, table in RECORD_TABLES.items():
            where = orphans_query(table)
            found = session.execute(
                text(f"select count(*) {where}"), {"module": module}
            ).scalar()
            total += found

            if apply and found:
                session.execute(text(f"delete {where}"), {"module": module})

            print(f"  {module:<17} {found} orphaned")

        if apply:
            session.commit()
            print(f"\nRemoved {total} notifications whose record no longer exists.")
        else:
            print(f"\n{total} found. Run again with --apply to remove them.")
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
