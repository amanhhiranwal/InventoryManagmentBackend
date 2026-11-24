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


def requisition_access(db) -> str:
    """My Requisitions becomes a tick on Roles & Access (requisition.create).

    It used to carry no permission at all, so every role had it and a
    super admin could not take it away. Once, on the first deploy with this
    release: point the existing menu row at the permission and give it to
    every role, so nobody loses the page. A marker setting stops a later
    run from handing it back to a role a super admin has since unticked.
    """

    from app.core.procurement import RAISE_REQUISITION
    from app.models.menu import MenuItem
    from app.services.procurement.common import grant_raise_requisition

    marker = "requisition_access_granted"

    menu = db.query(MenuItem).filter(MenuItem.path == "/procurement/requisitions").first()
    pointed = False
    if menu is not None and menu.permission_key != RAISE_REQUISITION:
        menu.permission_key = RAISE_REQUISITION
        pointed = True

    if db.query(AppSetting).filter(AppSetting.key == marker).first():
        db.commit()
        return "menu pointed at requisition.create" if pointed else "already set up"

    _, granted = grant_raise_requisition(db)
    db.add(AppSetting(key=marker, value="1"))
    db.commit()

    return f"given to {granted} role(s)" + ("; menu pointed at requisition.create" if pointed else "")


def departments_and_roles(db) -> str:
    """The departments and roles HR's employee sheet maps onto
    (app/services/org_setup.py), so people can be uploaded on any database.
    What exists is left exactly as it is."""

    from app.services.org_setup import ensure_departments, ensure_roles

    departments = ensure_departments(db)
    roles = ensure_roles(db)
    said = []
    if departments:
        said.append(f"{len(departments)} departments added")
    if roles:
        said.append(f"roles added: {', '.join(roles)}")
    return "; ".join(said) or "already there"


def coordinator_pending_approvals(db) -> str:
    """The Roles & Access tick that lets the business coordinator read the
    sales orders waiting for approval (order.pending_approval.read).

    The permission row is made if missing. Once, on the first deploy with
    this release, it is given to the Business Coordinator role; a marker
    setting stops a later run from handing it back to a role a super admin
    has since unticked.
    """

    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    from app.services.sales_order_service import SEE_PENDING_APPROVALS

    marker = "coordinator_pending_approvals_granted"

    permission = db.query(Permission).filter(Permission.permission_name == SEE_PENDING_APPROVALS).first()
    made = permission is None
    if made:
        permission = Permission(
            permission_name=SEE_PENDING_APPROVALS,
            module="order",
            description="See sales orders waiting for approval and which approver each is with (read only)",
        )
        db.add(permission)
        db.flush()

    if db.query(AppSetting).filter(AppSetting.key == marker).first():
        db.commit()
        return "permission added" if made else "already set up"

    role = db.query(Role).filter(Role.role_name == "Business Coordinator").first()
    granted = False
    if role is not None and not db.query(RolePermission).filter(
        RolePermission.role_id == role.id, RolePermission.permission_id == permission.id
    ).first():
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        granted = True

    db.add(AppSetting(key=marker, value="1"))
    db.commit()

    if role is None:
        return "permission ready; no Business Coordinator role to give it to"
    return "given to Business Coordinator" if granted else "Business Coordinator already has it"


def product_categories(db) -> str:
    """The Product Categories master, filled from the stock item list.
    Only names it does not have yet are added, so edits made under
    Masters are kept."""

    from app.services.procurement.product_categories import seed_from_file

    added = seed_from_file(db)
    return f"{added} added" if added else "already there"


def warehouses(db) -> str:
    """Every location gets a type, and every warehouse its standard bins.

    Locations saved before types existed have none; they were set up as
    warehouses, so that is what they become. A super admin can switch any
    of them to an office on the Locations screen.
    """

    from app.models.location import Location
    from app.services.warehouse_service import WarehouseService

    untyped = (
        db.query(Location)
        .filter(Location.location_type.is_(None))
        .update({Location.location_type: "WAREHOUSE"}, synchronize_session=False)
    )

    bins = 0
    for location in db.query(Location).all():
        bins += WarehouseService.ensure_default_bins(db, location)

    db.commit()

    if not untyped and not bins:
        return "nothing to do"
    return f"{untyped} location(s) typed as warehouse, {bins} standard bin(s) created"


STEPS = [
    ("Menus", menus),
    ("Requisitions", requisition_access),
    ("Org setup", departments_and_roles),
    ("Warranty terms", warranty_terms),
    ("Founder role", founder_role),
    ("Discount bands", discount_bands),
    ("Orphan approvals", orphan_approvals),
    ("Product categories", product_categories),
    ("Pending approvals", coordinator_pending_approvals),
    ("Warehouses", warehouses),
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
