"""What each sales role is granted, written down in one place.

Until now the sales roles' permissions were whatever a long line of seed
scripts and hand edits in Roles & Access had left behind, and it showed:

  * the AVP, the Zonal Heads and the Area Managers had no dashboard.read,
    so four of the seven sales people signed in and landed on Leads while
    the CEO alone got a dashboard;
  * all three held masters.menu without a single Masters child, so they
    carried the key to a door that was not there - and the day anyone gave
    them one Masters page, the whole menu would have appeared.

This states the intended set for each role and makes the database match:
anything listed and not held is granted, anything held and not listed is
taken away. Nothing else is touched, so a permission a super admin has
deliberately added on a role not named here survives.

The two desks are not here - seed_fulfilment_roles.py owns Accounts and
Inventory, because what they are granted is the point of that script.

    docker exec -w /app backend_app python seed_role_access.py
"""

import sys

from seed_sales_team import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    api,
    login,
    rows,
)

#: Everything a sales person needs to do their own job: their pipeline, the
#: catalogue to quote from, their team, and the numbers.
#:
#: The three ``.write`` entries are what separate somebody who works the
#: pipeline from somebody who merely has an account. Only ``lead`` had
#: create and update permissions before, so every other write route in the
#: pipeline fell back to "any signed-in user" - the accounts clerk and the
#: warehouse could both raise a proposal.
SALES_FLOOR = [
    "dashboard.read",
    "sales.menu",
    "lead.read", "lead.create", "lead.update",
    "opportunity.read", "opportunity.write",
    "quotation.read", "quotation.write",
    "order.read", "order.write",
    "customer.read",
    "inventory.menu", "inventory.read", "inventory.create",
    "user.read",
    "reports.read",
]

#: role -> what it holds. The hierarchy decides *whose* records each one
#: sees - a Zonal Head sees their own Area Managers' work and nobody
#: else's - so the permissions themselves are the same across the floor.
#: What separates the roles is the reporting line, not the grant list.
ROLES = {
    "CEO": SALES_FLOOR + [
        # The CEO signs off dealer pricing and the deepest discounts, and
        # brings customers in by the spreadsheet.
        "customer.bulk_upload",
    ],
    "AVP": SALES_FLOOR,
    "Zonal Head": SALES_FLOOR,
    "Area Manager": SALES_FLOOR,
}


def main() -> int:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    permissions = {
        p["permission_name"]: p
        for p in rows(api("get", "/rbac/permissions", admin))
    }

    roles = {
        r["role_name"]: r
        for r in rows(api("get", "/rbac/roles", admin))
    }

    for name, wanted in ROLES.items():
        role = roles.get(name)

        print(f"\n{name}")

        if role is None:
            print("  no such role - run check_and_seed_db.py first")
            continue

        held = {
            p["permission_name"]: p
            for p in rows(api("get", f"/rbac/roles/{role['id']}/permissions", admin))
        }

        for permission_name in wanted:
            if permission_name in held:
                continue

            permission = permissions.get(permission_name)

            if permission is None:
                print(f"  {permission_name} does not exist as a permission")
                continue

            response = api(
                "post",
                f"/rbac/roles/{role['id']}/permissions/{permission['id']}",
                admin,
            )

            if response.status_code >= 400:
                print(f"  {permission_name}: {response.status_code} {response.text[:100]}")
                continue

            print(f"  granted  {permission_name}")

        for permission_name, permission in sorted(held.items()):
            if permission_name in wanted:
                continue

            response = api(
                "delete",
                f"/rbac/roles/{role['id']}/permissions/{permission['id']}",
                admin,
            )

            if response.status_code >= 400:
                print(f"  {permission_name}: {response.status_code} {response.text[:100]}")
                continue

            print(f"  revoked  {permission_name}")

        print(f"  {len(wanted)} permissions")

    return 0


if __name__ == "__main__":
    sys.exit(main())
