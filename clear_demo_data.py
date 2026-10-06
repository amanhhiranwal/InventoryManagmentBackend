"""Empty the pipeline without emptying the system.

What a demo leaves behind is the pipeline: leads, the opportunities and
quotations they became, the orders and invoices under those, and the
approvals, activity trails and notifications hanging off all of it. What
a demo does not create is the system around it - the people, the roles
they hold, the catalogue they sell from, the masters the forms read, the
companies, the locations and the numbering.

So this clears the first list and leaves the second alone. Running it
gives a team their own CRM to start on rather than somebody else's
half-finished deals, and gives a test run a floor to start from.

Document counters are deliberately kept. They only ever go forward, and
that is the point of them: a quotation number that has been seen by a
customer must never be handed to a second quotation, whatever happens to
the row that carried it.

Customers live in Mongo rather than Postgres, so they are cleared there.

    python clear_demo_data.py --dry-run
    python clear_demo_data.py
    python clear_demo_data.py --include-test-accounts
"""

import argparse
import re
import sys

from sqlalchemy import text

from app.database.postgres import SessionLocal

#: Children before parents, so a foreign key never has to be argued with.
PIPELINE = [
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

#: Kept, and listed here so the choice is visible rather than implied.
KEPT = [
    "users, roles, permissions and who holds what",
    "the product catalogue and its stock",
    "masters: states, customer types, lead sources, product types, groups",
    "companies, locations, menus and app settings",
    "document counters, which only ever go forward",
]

#: An account a test run opened and did not close. Real people do not have
#: a unix timestamp in their address.
TEST_ACCOUNT = re.compile(r"(@example\.com$|\.[0-9a-f]{6}@|\d{9,})")


def clear_mongo(dry_run):
    try:
        from app.database.mongodb import sync_mongo_db as db
    except Exception as error:
        print(f"  customers: cannot reach Mongo ({str(error)[:60]})")
        return

    try:
        count = db["customers"].count_documents({})

        if not dry_run and count:
            db["customers"].delete_many({})

        print(f"  {'would clear' if dry_run else 'cleared'} customers: {count}")
    except Exception as error:
        print(f"  customers: cannot reach Mongo ({str(error)[:60]})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="count it all, delete nothing"
    )
    parser.add_argument(
        "--include-test-accounts",
        action="store_true",
        help="also remove accounts and roles a test run left behind",
    )
    args = parser.parse_args()

    db = SessionLocal()
    total = 0

    print("Pipeline\n")

    for table in PIPELINE:
        count = db.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()
        total += count

        if not args.dry_run and count:
            db.execute(text(f'DELETE FROM "{table}"'))

        print(f"  {'would clear' if args.dry_run else 'cleared'} {table:34s} {count}")

    clear_mongo(args.dry_run)

    if args.include_test_accounts:
        print("\nLeft behind by test runs\n")

        users = db.execute(
            text("SELECT id, email FROM users WHERE email IS NOT NULL")
        ).all()
        strays = [(i, e) for i, e in users if TEST_ACCOUNT.search(e)]

        for user_id, email in strays:
            if not args.dry_run:
                db.execute(
                    text("DELETE FROM user_roles WHERE user_id = :u"), {"u": user_id}
                )
                db.execute(
                    text("DELETE FROM user_companies WHERE user_id = :u"),
                    {"u": user_id},
                )
                db.execute(
                    text("UPDATE users SET reports_to_id = NULL WHERE reports_to_id = :u"),
                    {"u": user_id},
                )
                db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})

            print(f"  {'would remove' if args.dry_run else 'removed'} {email}")

        roles = db.execute(text("SELECT id, role_name FROM roles")).all()
        stray_roles = [(i, n) for i, n in roles if re.search(r"\d{9,}", n or "")]

        for role_id, name in stray_roles:
            if not args.dry_run:
                db.execute(
                    text("DELETE FROM role_permissions WHERE role_id = :r"),
                    {"r": role_id},
                )
                db.execute(
                    text("DELETE FROM user_roles WHERE role_id = :r"), {"r": role_id}
                )
                db.execute(text("DELETE FROM roles WHERE id = :r"), {"r": role_id})

            print(f"  {'would remove' if args.dry_run else 'removed'} role {name}")

        if not strays and not stray_roles:
            print("  nothing left behind")

    if not args.dry_run:
        db.commit()

    print(f"\n{total} pipeline rows {'to clear' if args.dry_run else 'cleared'}")
    print("\nKept:")
    for line in KEPT:
        print(f"  - {line}")


if __name__ == "__main__":
    main()
