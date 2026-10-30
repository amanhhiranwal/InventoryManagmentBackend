"""The Add Product pickers: every product on the shelf, for every salesperson.

Inventory is filed under a company, and most profiles carry none - so a
zonal head reading /inventory/items saw nothing, and their pickers were
empty. /inventory/catalogue is what the pickers read instead:

  * a salesperson with no company on their profile gets the same products
    as a super admin, one row per model, with stock and rates;
  * no serial numbers come with it;
  * an issued unit is not counted;
  * the Inventory page's own list stays narrowed to their companies;
  * signed out, nothing.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_picker_catalogue.py
"""

import sys

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, TEAM_PASSWORD, api, login, rows

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + name
        + (f"   [{detail}]" if detail and not condition else "")
    )


try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    zonal = login("zh.north@mailinator.com", TEAM_PASSWORD)

    from sqlalchemy import text

    from app.database.postgres import SessionLocal
    from app.services.company_scope_service import CompanyScopeService

    session = SessionLocal()
    zonal_id = session.execute(
        text("select id from users where email = :e"), {"e": "zh.north@mailinator.com"}
    ).scalar()
    companies = CompanyScopeService.assigned_company_ids(str(zonal_id), session)
    session.close()

    print("\nThe pickers' catalogue")

    check("the zonal head has no company on their profile", companies == [], str(companies))

    theirs = api("get", "/inventory/catalogue", zonal)
    everyone = api("get", "/inventory/catalogue", admin)

    check("the zonal head can read it", theirs.status_code == 200, f"{theirs.status_code} {theirs.text[:120]}")

    products = theirs.json().get("data") or []
    every = everyone.json().get("data") or []

    check("and gets products", len(products) > 0, str(len(products)))
    check(
        "the same products a super admin gets",
        [p["model_number"] for p in products] == [p["model_number"] for p in every],
        f"{len(products)} vs {len(every)}",
    )
    check(
        "one row per model",
        len({str(p["model_number"]).lower() for p in products}) == len(products),
    )
    check(
        "each with its stock and rates, and no serial numbers",
        all(
            {"name", "model_number", "rate", "dtp_rate", "instock", "hsn_code"} <= set(p)
            and "serial_numbers" not in p and "serial_number" not in p
            for p in products
        ),
        str(products[:1]),
    )

    from app.database.mongodb import sync_mongo_db

    items = sync_mongo_db["inventory_items"]
    on_shelf = sum(
        float((d.get("attributes") or {}).get("instock") or 0)
        for d in items.find({"issued": {"$ne": True}})
    )
    check(
        "issued units are not counted",
        abs(sum(float(p["instock"]) for p in products) - on_shelf) < 0.001,
        f"{sum(float(p['instock']) for p in products)} vs {on_shelf}",
    )

    # A product loaded with its Model Number left blank is still offered,
    # under its name.
    blank_name = "Picker Test Product No Model"
    added = items.insert_one({
        "name": blank_name, "serial_number": "PICKER-TEST-NO-MODEL", "product_type_code": "IFP",
        "category": "Test", "company_id": None,
        "attributes": {"model_number": "", "rate": 1000, "instock": 2},
    }).inserted_id
    try:
        offered = api("get", "/inventory/catalogue", zonal).json().get("data") or []
        check(
            "a product with a blank model number is offered under its name",
            any(p["name"] == blank_name and float(p["instock"]) == 2 for p in offered),
        )
    finally:
        items.delete_one({"_id": added})

    scoped = rows(api("get", "/inventory/items", zonal))
    check(
        "the Inventory page's list stays narrowed to their companies",
        all(row.get("company_id") in (None, "", *companies) for row in scoped),
        str(len(scoped)),
    )

    # A role that can only read leads is not let in by the inventory.read
    # aliases: the catalogue is for whoever adds products.
    from app.middleware.permission_middleware import ALIAS_MAP

    check(
        "reading leads alone is not enough to see the catalogue",
        not ({"lead.read", "lead.create", "sales.menu"} & ALIAS_MAP["product_picker.read"]),
    )

    inventory_desk = login("inventory@mailinator.com", TEAM_PASSWORD)
    check(
        "the inventory desk can read it",
        api("get", "/inventory/catalogue", inventory_desk).status_code == 200,
    )

    signed_out = api("get", "/inventory/catalogue", None)
    check("signed out, nothing", signed_out.status_code in (401, 403), str(signed_out.status_code))

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
