"""Seed the two desks that handle an order after it is approved.

    Accounts    confirm the money has arrived, and close the order when the
                balance is settled.
    Inventory   confirm the stock, take the order into procurement, and
                send it out.

Both are ordinary roles in Roles & Access: a super admin can rename them,
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
    "payment_desk.read": ("Fulfilment", "Work the accounts desk: verify payments, close orders"),
    "procurement_desk.read": ("Fulfilment", "Work the procurement desk: confirm stock, dispatch, deliver"),
    # The catalogue side of the warehouse's job. inventory.read and
    # inventory.create already existed; the other two never did, so nobody
    # but a super admin could correct a stock figure.
    "inventory.update": ("Inventory", "Edit a product and its stock level"),
    "inventory.delete": ("Inventory", "Remove a product from the catalogue"),
}

#: role -> what it is for, and what it is granted.
ROLES = {
    # Deliberately without dashboard.read: the sales dashboard is not their
    # screen, and the app sends a role that lacks it to the first page it
    # does have - which is their own desk.
    "Accounts": (
        "Confirms payments against proforma invoices and closes settled orders.",
        ["payment_desk.read"],
    ),
    # The warehouse keeps the catalogue as well as working the desk: they
    # are the ones who know what is on the shelf, so they add and correct
    # the products rather than asking a super admin to do it.
    "Inventory": (
        "Confirms stock, takes orders into procurement, dispatches and "
        "delivers, and keeps the product catalogue.",
        [
            "procurement_desk.read",
            "inventory.read", "inventory.create", "inventory.update",
            "inventory.delete",
            # The product type, category group and unit lookups the
            # catalogue screen needs come with inventory.read - granting
            # the Masters pages themselves would put the warehouse in
            # Masters, which is somebody else's job.
        ],
    ),
}

#: One person per desk, so the screens can actually be signed in to.
STAFF = [
    ("Neha", "Bansal", "Accounts", "accounts@mailinator.com"),
    ("Rahul", "Verma", "Inventory", "inventory@mailinator.com"),
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

        grant(admin, roles[name], grants, existing)

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
            print(f"  {email} already there")
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
            "employee_id": f"SYN-{role_name.upper()}",
            "phone_number": "9800000010",
            "role_ids": [str(role["id"])],
            "company_ids": every_company,
        })

        if created.status_code >= 400:
            print(f"  {email}: {created.status_code} {created.text[:160]}")
            continue

        print(f"  {email} created as {role_name}")

    stock(admin)

    print("\nSign in with:")

    for _, _, role_name, email in STAFF:
        print(f"  {role_name:<10} {email}  /  {TEAM_PASSWORD}")

    return 0


#: What the procurement desk checks an order against. The serial numbers
#: match the SKUs the demo quotations and orders carry, because that is how
#: the two sides are keyed.
STOCK = [
    ("Interactive Flat Panel 75in", "NX-9K-QIFP75-EX", 12),
    ("Interactive Flat Panel 65in", "NX-9K-QIFP65-EX", 3),
    ("OPS PC i5", "NX-OPS-I5-8-256", 0),
]

PRODUCT_TYPE = {
    "name": "Display Systems",
    "code": "DISPLAY",
    "category": "Interactive Panels",
}


def stock(admin) -> None:
    """Put something on the shelf, so the procurement desk has an answer.

    Without any inventory every line reads "not in the catalogue", which
    is honest but shows nothing. One item is deliberately short and one is
    out altogether, so the desk's shortfall warning can be seen working.
    """

    print("\nStock for the procurement desk")

    types = {t["code"]: t for t in rows(api("get", "/product-types/", admin))}

    if PRODUCT_TYPE["code"] not in types:
        created = api("post", "/product-types/", admin, json=PRODUCT_TYPE)

        if created.status_code >= 400:
            print(f"  could not add the product type: {created.text[:120]}")
            return

        print(f"  product type {PRODUCT_TYPE['code']} created")

    held = {
        str(item.get("serial_number") or "").upper()
        for item in rows(api("get", "/inventory/items", admin))
    }

    for name, serial, quantity in STOCK:
        if serial.upper() in held:
            print(f"  {serial} already stocked")
            continue

        created = api("post", "/inventory/items", admin, json={
            "name": name,
            "serial_number": serial,
            "product_type_code": PRODUCT_TYPE["code"],
            "category": PRODUCT_TYPE["category"],
            "attributes": {
                "rate": 185000,
                "unit": "Nos",
                "instock": quantity,
                "case_size": 1,
            },
        })

        if created.status_code >= 400:
            print(f"  {serial}: {created.status_code} {created.text[:120]}")
            continue

        print(f"  {serial} stocked, {quantity} in hand")


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
