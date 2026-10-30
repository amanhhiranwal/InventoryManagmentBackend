"""Seed the desks that handle an order after it is approved.

    Business Coordinator  record the advance against the payment terms,
                          accept it or hold the order, and process it on
                          to accounts. (Business Coordinator menu)
    Accounts              confirm the money has arrived, and close the
                          order when the balance is settled.
    Production Tech Head  confirm the stock and take the order through
                          procurement until it is ready. (Technology menu)
    Logistics             send a ready order out and confirm it was
                          delivered. (SCM menu)
    Inventory             keep the product catalogue - no desk. (SCM menu)

All are ordinary roles in Roles & Access: a super admin can rename them,
grant them more, or take them away. What they are granted here is
deliberately narrow - an accounts person sees their own desk and the
tracking board, and nothing else of the sales CRM, not even the dashboard.

Everyone else follows an order on the sales order's own Order Process
panel, which they already have - there is no separate tracking screen.

Safe to run again: anything already there is left alone.

    docker exec -w /app backend_app python seed_fulfilment_roles.py
"""

import sys

from seed_sales_team import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    TEAM_PASSWORD,
    api,
    login,
    rows,
)

def one(response):
    """The created record, whichever envelope the endpoint happens to use."""

    body = response.json()

    return body.get("data", body) if isinstance(body, dict) else body


#: permission -> which module it belongs to, for the Roles & Access screen.
PERMISSIONS = {
    "coordination_desk.read": ("Fulfilment", "Work the business coordinator desk: accept advances, process orders"),
    "payment_desk.read": ("Fulfilment", "Work the accounts desk: verify payments, close orders"),
    "production_desk.read": ("Fulfilment", "Work the production desk: confirm stock, take orders through procurement"),
    "logistics_desk.read": ("Fulfilment", "Work the logistics desk: dispatch orders and confirm delivery"),
    # The catalogue side of the warehouse's job. inventory.read and
    # inventory.create already existed; the other two never did, so nobody
    # but a super admin could correct a stock figure.
    "inventory.update": ("Inventory", "Edit a product and its stock level"),
    "inventory.delete": ("Inventory", "Remove a product from the catalogue"),
    # Loading a supplier's sheet is a different act from adding a product:
    # one row can become forty records, so it is granted on its own rather
    # than coming free with inventory.create.
    "inventory.bulk_upload": (
        "Inventory",
        "Load a sheet of products from Excel or CSV",
    ),
    # The record of every unit issued to an order. Granted to no role here:
    # a super admin sees it, and Roles & Access gives it to anyone else.
    "stock_out.read": (
        "Inventory",
        "See every unit issued from inventory to an order: serial, order, customer and destination",
    ),
}

#: role -> what it is for, and what it is granted.
ROLES = {
    # The only role that can process an approved order on to accounts.
    "Business Coordinator": (
        "Checks the advance against the payment terms, accepts it or holds "
        "the order, and processes approved orders on to accounts.",
        ["coordination_desk.read"],
    ),
    # Deliberately without dashboard.read: the sales dashboard is not their
    # screen, and the app sends a role that lacks it to the first page it
    # does have - which is their own desk.
    "Accounts": (
        "Confirms payments against proforma invoices and closes settled orders.",
        ["payment_desk.read"],
    ),
    # Took over the procurement desk from the warehouse, renamed Production
    # and filed under Technology. The desk shows the stock against each
    # order, so the catalogue itself stays the Inventory role's.
    "Production Tech Head": (
        "Confirms stock, names a serial for every unit and marks orders "
        "ready to dispatch.",
        ["production_desk.read"],
    ),
    "Logistics": (
        "Uploads the dispatch documents, dispatches ready orders and marks "
        "them out for delivery and delivered.",
        ["logistics_desk.read"],
    ),
    # The warehouse keeps the catalogue and nothing else: they are the ones
    # who know what is on the shelf, so they add and correct the products
    # rather than asking a super admin to do it.
    "Inventory": (
        "Keeps the product catalogue and the stock levels.",
        [
            "inventory.read", "inventory.create", "inventory.update",
            "inventory.delete", "inventory.bulk_upload",
            # The product type, category group and unit lookups the
            # catalogue screen needs come with inventory.read - granting
            # the Masters pages themselves would put the warehouse in
            # Masters, which is somebody else's job.
        ],
    ),
}

#: Taken away from a role when this runs: role -> permissions it no longer
#: holds by default. A super admin can grant them back in Roles & Access.
REVOKED = {
    # The desk went to the Production Tech Head; the warehouse keeps the
    # catalogue only.
    "Inventory": ["procurement_desk.read"],
    # Held by the first seed of this role; the catalogue is Inventory's.
    "Production Tech Head": ["inventory.read"],
    # Copied from the CEO when the founder's role was made. The Inventory
    # page is the Inventory role's; the founder still prices proposals off
    # the catalogue through sales.menu.
    "Founder": ["inventory.menu", "inventory.read", "inventory.create"],
}

#: Descriptions earlier versions of this script wrote, replaced when a role
#: still carries one. A description a super admin has written is kept.
OLD_DESCRIPTIONS = {
    "Production Tech Head": {
        "Confirms stock and takes orders through procurement until they "
        "are ready to dispatch.",
    },
    "Logistics": {
        "Dispatches ready orders and confirms they reached the client.",
    },
    "Inventory": {
        "Confirms stock, takes orders into procurement, dispatches and delivers.",
        "Confirms stock, takes orders into procurement, dispatches and "
        "delivers, and keeps the product catalogue.",
    },
}

#: One person per desk, so the screens can actually be signed in to.
STAFF = [
    ("Pooja", "Sharma", "Business Coordinator", "coordinator@mailinator.com"),
    ("Neha", "Bansal", "Accounts", "accounts@mailinator.com"),
    ("Rahul", "Verma", "Inventory", "inventory@mailinator.com"),
    ("Arjun", "Mehta", "Production Tech Head", "production@mailinator.com"),
    ("Kiran", "Rao", "Logistics", "logistics@mailinator.com"),
]


def main() -> int:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    # ------------------------------------------------------- permissions
    existing = {
        p["permission_name"]: p
        for p in rows(api("get", "/rbac/permissions", admin))
    }

    print("Permissions")

    for name, (module, description) in PERMISSIONS.items():
        if name in existing:
            print(f"  {name} already there")
            continue

        created = api("post", "/rbac/permissions", admin, json={
            "permission_name": name,
            "module": module,
            "description": description,
        })

        if created.status_code >= 400:
            print(f"  {name}: {created.status_code} {created.text[:120]}")
            continue

        existing[name] = one(created)
        print(f"  {name} created")

    # ------------------------------------------------------------- roles
    roles = {
        r["role_name"]: r
        for r in rows(api("get", "/rbac/roles", admin))
    }

    print("\nRoles")

    for name, (description, grants) in ROLES.items():
        if name not in roles:
            created = api("post", "/rbac/roles", admin, json={
                "role_name": name,
                "description": description,
            })

            if created.status_code >= 400:
                print(f"  {name}: {created.status_code} {created.text[:120]}")
                continue

            roles[name] = one(created)
            print(f"  {name} created")
        else:
            print(f"  {name} already there")

            if roles[name].get("description") in OLD_DESCRIPTIONS.get(name, ()):
                updated = api("put", f"/rbac/roles/{roles[name]['id']}", admin, json={
                    "role_name": name,
                    "description": description,
                })

                print(
                    "    description brought up to date"
                    if updated.status_code < 400
                    else f"    description: {updated.status_code} {updated.text[:100]}"
                )

        grant(admin, roles[name], grants, existing)

    # Taken away from any role that still holds them, including roles this
    # script does not create (the founder's).
    for name, names in REVOKED.items():
        if name in roles:
            print(f"  {name}")
            revoke(admin, roles[name], names, existing)

    # ------------------------------------------------------------- staff
    print("\nDesk staff")

    people = {
        u["email"]: u
        for u in rows(api("get", "/users/", admin, params={"size": 200}))
    }

    # Both desks serve the whole business, so they belong to every company.
    # Without this the warehouse cannot file a product under anything and
    # the catalogue is closed to them.
    every_company = [
        str(c["id"])
        for c in rows(api("get", "/companies/", admin, params={"size": 200}))
    ]

    for first, last, role_name, email in STAFF:
        if email in people:
            # "Already there" is not the same as "set up". An account that
            # predates the line below, or one somebody has edited, can be
            # in no company at all - and then the warehouse cannot file a
            # product under anything, which is their whole job. The role
            # and the companies go back on together, because this PUT
            # replaces the record rather than adding to it.
            who = people[email]
            missing = [c for c in every_company if c not in (who.get("company_ids") or [])]

            if not missing:
                print(f"  {email} already there")
                continue

            role = roles.get(role_name)

            fixed = api("put", f"/users/{who['id']}", admin, json={
                "first_name": who.get("first_name") or first,
                "last_name": who.get("last_name") or last,
                "email": email,
                "phone_number": who.get("phone_number") or "9800000010",
                "employee_id": who.get("employee_id") or employee_id(role_name),
                "location": who.get("location"),
                "role_ids": who.get("role_ids") or ([str(role["id"])] if role else []),
                "company_ids": every_company,
                "reports_to_id": who.get("reports_to_id"),
            })

            print(
                f"  {email} put into {len(missing)} more compan"
                f"{'y' if len(missing) == 1 else 'ies'}"
                if fixed.status_code < 400
                else f"  {email}: {fixed.status_code} {fixed.text[:120]}"
            )
            continue

        role = roles.get(role_name)

        if role is None:
            print(f"  {email}: no {role_name} role to give them")
            continue

        created = api("post", "/users/", admin, json={
            "first_name": first,
            "last_name": last,
            "email": email,
            "password": TEAM_PASSWORD,
            "employee_id": employee_id(role_name),
            "phone_number": "9800000010",
            "role_ids": [str(role["id"])],
            "company_ids": every_company,
        })

        if created.status_code >= 400:
            print(f"  {email}: {created.status_code} {created.text[:160]}")
            continue

        print(f"  {email} created as {role_name}")

    print("\nSign in with:")

    for _, _, role_name, email in STAFF:
        print(f"  {role_name:<20} {email}  /  {TEAM_PASSWORD}")

    return 0


def employee_id(role_name: str) -> str:
    """SYN-ACCOUNTS, SYN-PRODUCTION-TECH-HEAD - one per desk."""

    return f"SYN-{role_name.upper().replace(' ', '-')}"


def revoke(admin, role, permission_names, permissions) -> None:
    """Untick permissions on a role, where it still holds them."""

    held = {
        p["permission_name"]
        for p in rows(
            api("get", f"/rbac/roles/{role['id']}/permissions", admin)
        )
    }

    for name in permission_names:
        permission = permissions.get(name)

        if name not in held or permission is None:
            continue

        response = api(
            "delete",
            f"/rbac/roles/{role['id']}/permissions/{permission['id']}",
            admin,
        )

        if response.status_code >= 400:
            print(f"    {name}: {response.status_code} {response.text[:100]}")
        else:
            print(f"    took away {name}")


def grant(admin, role, permission_names, permissions, quiet: bool = False) -> None:
    """Tick permissions for a role, leaving anything already ticked alone."""

    held = {
        p["permission_name"]
        for p in rows(
            api("get", f"/rbac/roles/{role['id']}/permissions", admin)
        )
    }

    for name in permission_names:
        if name in held:
            continue

        permission = permissions.get(name)

        if permission is None:
            if not quiet:
                print(f"    {name} does not exist yet")
            continue

        response = api(
            "post",
            f"/rbac/roles/{role['id']}/permissions/{permission['id']}",
            admin,
        )

        if response.status_code >= 400:
            print(f"    {name}: {response.status_code} {response.text[:100]}")
        elif not quiet:
            print(f"    granted {name}")


if __name__ == "__main__":
    sys.exit(main())
