"""Departments and roles from HR's employee master sheet.

The sheet ("Master Data for Employee") has a Department and a Designation
for every employee. This sets up what the CRM needs before people are
added by hand:

  * The departments, with the sheet's spellings tidied up: "Curriculuam"
    is Curriculum, "HR" and "Human Resources" are one department, and the
    three logistics variants are one.

  * The roles. A role here is what someone may open and do, not their job
    title - the sheet has some ninety designations, and ninety roles would
    be unworkable on Roles & Access. Each designation maps onto one role
    (ROLE_FOR below); the sales chart keeps the roles it already has.

New roles and what they are given (a super admin can change any of it on
Roles & Access afterwards; every role can also raise purchase requisitions):

  Employee          the everyday user: raises purchase requisitions
  Procurement       the procurement desk, vendors and purchase orders
  Procurement Head  as Procurement, plus approving vendors and keeping
                    the departments and PR approval matrix
  HR & Admin        users, the reporting chart and the Masters lists
  Marketing         as Employee, plus leads (campaign enquiries)

Nobody is created or changed. Run it as often as you like; what exists is
left alone and the run says what it added.

    docker exec -w /app backend_app python seed_departments_and_roles.py
    docker exec -w /app backend_app python seed_departments_and_roles.py --sheet /tmp/master.xlsx

With --sheet it also reports, for that sheet, which role and department
each designation lands on - counts only, no names - so a designation the
map does not know shows up before anyone is added.
"""

import argparse
import collections
import sys

from app.database.postgres import SessionLocal
from app.models.role import Role
from app.services.org_setup import (
    DEFAULT_ROLE,
    ROLE_FOR,
    department_for,
    ensure_departments,
    ensure_roles,
    role_for,
    tidy,
)


def report(path: str, db) -> int:
    """How the sheet's designations and departments land. Counts only."""

    import openpyxl

    sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = [r for r in sheet.iter_rows(values_only=True) if any(c is not None for c in r)]
    header = [tidy(c).lower() for c in rows[0]]
    people = [dict(zip(header, r)) for r in rows[1:]]
    active = [p for p in people if tidy(p.get("status")).lower() == "active"]
    known_roles = {r.role_name for r in db.query(Role).all()}

    by_role = collections.Counter(role_for(p.get("designation")) for p in active)
    print(f"\nThe sheet: {len(people)} rows, {len(active)} active. Active people by role:")
    for role, count in by_role.most_common():
        print(f"  {role:24s} {count:3d}" + ("" if role in known_roles else "   <- role missing"))

    unmapped = collections.Counter(
        tidy(p.get("designation")) for p in active if tidy(p.get("designation")).lower() not in ROLE_FOR
    )
    print(f"\nActive designations with no entry, so {DEFAULT_ROLE}:")
    for designation, count in sorted(unmapped.items()):
        print(f"  {designation} ({count})")

    missing_dept = sum(1 for p in active if not department_for(p.get("department")))
    unknown = sorted({tidy(p.get("department")) for p in active if tidy(p.get("department"))
                      and not department_for(p.get("department"))})
    print(f"\nActive people with no department in the sheet: {missing_dept}")
    if unknown:
        print("Departments the map does not know:", ", ".join(unknown))
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheet", help="the employee master .xlsx, to report how it maps")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        added = ensure_departments(db)
        print("Departments:", ", ".join(added) if added else "all there already")
        added = ensure_roles(db)
        print("Roles:", ", ".join(added) if added else "all there already")
        return report(args.sheet, db) if args.sheet else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
