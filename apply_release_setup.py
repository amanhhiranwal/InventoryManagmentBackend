"""Everything a release needs doing to the data, once, on every deploy.

The pipeline already syncs the schema and seeds the roles. What it did
not do is the handful of one-off data changes a release sometimes needs:
a new role, a changed setting, a menu that has to exist before anybody
can reach the screen that was just shipped.

Those were a list in a document for somebody to remember. This is that
list as a script, so pushing to main is actually enough and nobody has
to recall that the discount bands live in a table rather than in code.

Everything here is idempotent and says what it did. Run it as often as
you like; a second run reports that there was nothing to do.

    docker exec -w /app backend_app python apply_release_setup.py

What it does NOT do, on purpose:

  * Import people. That needs the staffing sheet, which is not in the
    repository, and it generates real passwords that have to be handed
    out. See import_users_from_sheet.py.
  * Drop the old warranty rate columns. That one is destructive and
    carries data across, so it stays a decision somebody makes with a
    dry run in front of them. See migrations/drop_warranty_term_rates.py.
"""

import json
import sys

from app.core.approvals import DEFAULT_DISCOUNT_BANDS
from app.database.postgres import SessionLocal
from app.models.app_setting import AppSetting


def discount_bands(db) -> str:
    """The bands the approval chain reads, which live in a setting.

    Not in code: a super admin can change them from Masters, so the code
    only holds the starting point. A database carrying the old 15% keeps
    using it however many times the new default is deployed, which is
    exactly the sort of thing nobody notices until an order goes to the
    wrong desk.
    """

    wanted = [
        {"role": role, "to_percent": bound}
        for bound, role in DEFAULT_DISCOUNT_BANDS
    ]

    row = db.query(AppSetting).filter(AppSetting.key == "discount_bands").first()

    if row is None:
        db.add(AppSetting(key="discount_bands", value=json.dumps(wanted)))
        db.commit()
        return f"set for the first time: {json.dumps(wanted)}"

    try:
        held = json.loads(row.value or "[]")
    except Exception:  # noqa: BLE001 - a bad value is replaced
        held = None

    if held == wanted:
        return "already correct"

    # Only replaced when it still matches a set we shipped. Somebody who
    # has tuned their own bands keeps them.
    known_old = [
        [{"role": "AVP", "to_percent": 15.0},
         {"role": "CEO", "to_percent": 20.0},
         {"role": "Founder", "to_percent": None}],
    ]

    if held not in known_old:
        return (
            f"left alone - it has been changed from a shipped default "
            f"({row.value[:60]})"
        )

    row.value = json.dumps(wanted)
    db.commit()

    return f"moved off the old default to {json.dumps(wanted)}"


def founder_role(db) -> str:
    """The role, its place above the CEO, and what it is granted."""

    import io
    from contextlib import redirect_stdout

    import seed_founder_role

    said = io.StringIO()

    with redirect_stdout(said):
        role = seed_founder_role.ensure_role(db)
        seed_founder_role.ensure_top_of_chart(db, role)
        seed_founder_role.ensure_permissions(db, role)

    return "; ".join(
        line.strip() for line in said.getvalue().splitlines() if line.strip()
    )


def menus(db) -> str:
    """Seeded on the first request that reads them, but not before.

    Doing it here means the first person to sign in after a deploy does
    not pay for it, and a menu that failed to seed shows up in the
    deploy log rather than as a missing item somebody reports a week
    later.
    """

    from app.models.menu import MenuItem
    from app.services.menu_service import MenuService

    before = db.query(MenuItem).count()
    MenuService.ensure_default_menus(db)
    after = db.query(MenuItem).count()

    return f"{after - before} added ({after} in total)" if after != before else "already there"


def warranty_terms(db) -> str:
    """The lengths every document offers. Their price is per product."""

    from app.models.warranty_term import WarrantyTerm
    from app.services.warranty_term_service import WarrantyTermService

    before = db.query(WarrantyTerm).count()
    WarrantyTermService.seed_defaults(db)
    after = db.query(WarrantyTerm).count()

    return f"{after - before} added ({after} in total)" if after != before else "already there"


def orphan_approvals(db) -> str:
    """Approvals left behind by documents that no longer exist.

    An approval used to survive the deletion of the order it was raised
    on. Ids come from a sequence, so an approval that outlives its order
    sits waiting to be read as the approval of whatever order is handed
    that id next - and a brand new order then comes up already approved,
    with no step for the CEO or the founder in it, which is exactly what
    somebody sending one up for the first time sees.

    Deleting an order takes its approvals with it now. This clears the
    ones that were already orphaned before that was true, on whichever
    database the release lands on, so the fix does not need anybody to
    open a psql prompt against production.

    Only rows whose document is genuinely gone. An approval on a document
    that still exists is a record of a decision and is never touched.
    """

    from sqlalchemy import text

    removed = 0

    # Each document type against the table it points at. A type whose
    # table is missing is skipped rather than guessed at.
    for document_type, table in (
        ("SALES_ORDER", "sales_order"),
        ("QUOTATION", "sales_quotation"),
    ):
        exists = db.execute(
            text("select to_regclass(:name)"), {"name": table}
        ).scalar()

        if not exists:
            continue

        removed += db.execute(
            text(
                f"""
                delete from sales_approval a
                 where a.document_type = :document_type
                   and not exists (
                       select 1 from {table} d where d.id = a.document_id
                   )
                """
            ),
            {"document_type": document_type},
        ).rowcount

    db.commit()

    return f"{removed} cleared" if removed else "none left behind"


STEPS = [
    ("Menus", menus),
    ("Warranty terms", warranty_terms),
    ("Founder role", founder_role),
    ("Discount bands", discount_bands),
    ("Orphan approvals", orphan_approvals),
]


def main() -> int:
    db = SessionLocal()
    failures = 0

    print("Applying release setup\n")

    for name, step in STEPS:
        try:
            print(f"  {name:16s} {step(db)}")
        except Exception as error:  # noqa: BLE001
            # One step failing must not stop the rest, and must not fail
            # the deploy: the application is already running by now, and
            # a missing menu is not worth rolling a release back for.
            failures += 1
            db.rollback()
            print(f"  {name:16s} FAILED - {str(error).splitlines()[0][:90]}")

    if failures:
        print(f"\n{failures} step(s) failed. The deploy is not rolled back for this;")
        print("fix it and run this script again.")
    else:
        print("\nAll release setup applied.")

    # Always zero: this is housekeeping after a deploy that has already
    # succeeded, and failing the pipeline here helps nobody.
    return 0


if __name__ == "__main__":
    sys.exit(main())
