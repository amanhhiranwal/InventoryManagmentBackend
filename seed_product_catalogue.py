"""Stock the warehouse properly, so inventory management has something to manage.

Three products was enough to prove the procurement desk read the shelf. It
is not enough to see the shelf *working*: you cannot tell a healthy stock
level from a thin one, the Inventory screen's filters have nothing to
filter, and "how much is left after fulfilling the order" is a question
about one number rather than a catalogue.

So this puts up a whole integrator's catalogue - displays, computing,
audio, networking, licences, mounts and spares - deliberately uneven:

    plenty       most lines, so a normal order sails through
    thin         75in has 12, 86in has 6, UPS has 1 - a big order bites
    nothing      OPS i5, dongles and remotes are out, so the desk's
                 shortfall warning can be seen doing its job

Two lines are filed under one company each, so per-company products can be
seen being kept apart. Everything else is shared, which is what the demo
orders need.

It also corrects the product types. Their Category field is picked from the
Category Group master, and the one seeded earlier held "Interactive Panels"
- which is not a category group at all, so the Inventory table's Category
Group column was showing something that exists nowhere in Masters.

Safe to run again: a product already on the shelf is left exactly as it is,
stock included, so this can never undo a day's picking.

RETIRED. seed_synergy_catalogue.py replaces it - the real catalogue, at
the rates and HSN codes on the stock dashboard. This script now refuses to
run, because restoring the invented NX- lines would leave quotations
pricing against products nobody sells.

    docker exec -w /app backend_app python seed_synergy_catalogue.py
"""

import sys

from seed_sales_team import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    api,
    login,
    rows,
)

#: Units the catalogue below is priced in, added to the Units master if it
#: does not have them. Exactly the ones used and no more: a master full of
#: units nothing is priced in is just a longer dropdown to scroll past.
#: Box is already there, so it is not repeated.
UNITS = ["Nos", "Set", "Licence", "Pack"]

#: code -> name, and which Category Group it belongs to. The category must
#: be the name of a real category group: that is what the Product Type form
#: offers, and what the Inventory table prints under "Category Group".
PRODUCT_TYPES = [
    ("DISPLAY", "Display Systems", "Hardware Solutions"),
    ("COMPUTE", "Computing Hardware", "Hardware Solutions"),
    ("AUDIO", "Audio & Conferencing", "Consumer Electronics"),
    ("NETWORK", "Networking", "Hardware Solutions"),
    ("SOFTWARE", "Software & Licences", "Software & Licenses"),
    ("INFRA", "Mounts & Site Infrastructure", "Office Infrastructure"),
    ("SPARES", "Accessories & Spares", "General"),
]

#: type code, name, serial (the SKU orders are keyed on), rate, unit,
#: stock in hand, how many come in a case.
#:
#: The first three are the ones the demo quotations and orders quote, so
#: their serials must not change - that is how an order line finds its
#: product.
CATALOGUE = [
    # ---------------------------------------------------------- displays
    ("DISPLAY", "Interactive Flat Panel 75in",      "NX-9K-QIFP75-EX",  185000, "Nos", 12, 1),
    ("DISPLAY", "Interactive Flat Panel 65in",      "NX-9K-QIFP65-EX",  142000, "Nos",  3, 1),
    ("DISPLAY", "Interactive Flat Panel 86in",      "NX-9K-QIFP86-EX",  264000, "Nos",  6, 1),
    ("DISPLAY", "Digital Signage Display 55in",     "NX-DS-55-4K",       78000, "Nos",  9, 1),
    ("DISPLAY", "Laser Projector 4K",               "NX-PJ-4K-LSR",      96000, "Nos",  2, 1),
    # --------------------------------------------------------- computing
    ("COMPUTE", "OPS PC i5 8GB 256GB",              "NX-OPS-I5-8-256",   42000, "Nos",  0, 1),
    ("COMPUTE", "OPS PC i7 16GB 512GB",             "NX-OPS-I7-16-512",  68000, "Nos",  5, 1),
    ("COMPUTE", "Teacher Laptop 14in i5",           "NX-LT-14-I5",       54000, "Nos",  8, 1),
    ("COMPUTE", "Mini PC Classroom Node",           "NX-MPC-N100",       27500, "Nos", 14, 1),
    # ------------------------------------------------------------- audio
    ("AUDIO",   "Ceiling Microphone Array",         "NX-MIC-CEIL-4",     38000, "Set",  7, 1),
    ("AUDIO",   "Classroom Soundbar 120W",          "NX-SB-120",         21500, "Nos", 11, 2),
    ("AUDIO",   "Conference Camera 4K PTZ",         "NX-CAM-PTZ-4K",     89000, "Nos",  4, 1),
    ("AUDIO",   "Wireless Presentation Dongle",     "NX-WPD-BT4",        12500, "Nos",  0, 4),
    # -------------------------------------------------------- networking
    ("NETWORK", "Managed Switch 24-Port PoE",       "NX-SW-24-POE",      46000, "Nos",  6, 1),
    ("NETWORK", "Wi-Fi 6 Access Point",             "NX-AP-WIFI6",       14500, "Nos", 18, 4),
    ("NETWORK", "CAT6 Cable Roll 305m",             "NX-CBL-CAT6-305",    9800, "Box",  5, 1),
    # ---------------------------------------------------------- licences
    ("SOFTWARE", "Classroom Suite Licence 1 Year",  "NX-SW-CLASS-1Y",     3200, "Licence", 40, 1),
    ("SOFTWARE", "Device Management Licence 3 Year", "NX-SW-MDM-3Y",      7400, "Licence", 25, 1),
    # ------------------------------------------------------------ mounts
    ("INFRA",   "Motorised Floor Stand",            "NX-MNT-FLR-MOT",    34000, "Nos",  3, 1),
    ("INFRA",   "Heavy Duty Fixed Wall Mount",      "NX-MNT-WALL-HD",     6500, "Nos", 22, 2),
    ("INFRA",   "Online UPS 3kVA",                  "NX-UPS-3K",         38500, "Nos",  1, 1),
    # ------------------------------------------------------------ spares
    ("SPARES",  "Stylus Pen Pack of 4",             "NX-ACC-STY-4",       1800, "Pack", 30, 1),
    ("SPARES",  "Universal Remote Control",         "NX-ACC-RMT",          950, "Nos",  0, 1),
    ("SPARES",  "HDMI 2.1 Cable 5m",               "NX-ACC-HDMI-5M",      2400, "Nos", 16, 5),
]

#: serial -> the company that alone stocks it. Everything not named here is
#: shared, which is what the demo orders across both companies rely on.
#: These two exist so per-company products can be seen being kept apart:
#: signed in as a North salesperson the South kit is not in the catalogue
#: at all, and the other way round.
COMPANY_ONLY = {
    "NX-DEMO-NORTH": "Synergy North Agro",
    "NX-DEMO-SOUTH": "Synergy South Seeds",
}

DEMO_KITS = [
    ("SPARES", "North Region Demo Kit", "NX-DEMO-NORTH", 55000, "Set", 2, 1),
    ("SPARES", "South Region Demo Kit", "NX-DEMO-SOUTH", 55000, "Set", 2, 1),
]


def main() -> int:
    # RETIRED. Superseded by seed_synergy_catalogue.py, which puts up the
    # products we actually sell with the rates and HSN codes written on the
    # Noida 65 stock dashboard. Running this would put the twenty-six
    # invented NX- lines back on the shelf, and every quotation raised off
    # one would price against a product that does not exist.
    #
    # Left in the tree rather than deleted because the product types, units
    # and category-group realignment below are still the only written record
    # of how those masters are meant to line up.
    if "--i-know-this-is-retired" not in sys.argv:
        print(
            "  seed_product_catalogue.py is retired.\n"
            "  Use: docker exec -w /app backend_app python "
            "seed_synergy_catalogue.py\n"
            "  It seeds the real catalogue; this one would restore the "
            "withdrawn NX- demo products."
        )
        return 1

    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    units(admin)
    types = product_types(admin)
    catalogue(admin, types)
    realign(admin, types)

    if "--restock" in sys.argv:
        restock(admin)

    report(admin)

    return 0


def restock(admin) -> None:
    """Put every line back to the figure this file intends.

    Ordinary runs never touch stock, because overwriting a real count
    would undo a day's picking. But the test suites dispatch demo orders,
    and dispatch takes stock off the shelf for real - so after a test run
    the demo shelf is a little emptier than the script says. This is the
    way back, and it is opt-in:

        docker exec -w /app backend_app python seed_product_catalogue.py --restock
    """

    print("\nRestocking to the seeded figures")

    intended = {
        serial.upper(): quantity
        for _, _, serial, _, _, quantity, _ in CATALOGUE + DEMO_KITS
    }

    put_back = 0

    for item in rows(api("get", "/inventory/items", admin)):
        serial = str(item.get("serial_number") or "").upper()
        want = intended.get(serial)

        if want is None:
            continue

        attributes = dict(item.get("attributes") or {})
        held = float(attributes.get("instock") or 0)

        if held == want:
            continue

        attributes["instock"] = want

        fixed = api("put", f"/inventory/items/{item['_id']}", admin, json={
            "name": item.get("name"),
            "serial_number": item.get("serial_number"),
            "product_type_code": item.get("product_type_code"),
            "category": item.get("category"),
            "attributes": attributes,
            "company_id": item.get("company_id"),
        })

        if fixed.status_code >= 400:
            print(f"  {serial}: {fixed.status_code} {fixed.text[:120]}")
            continue

        put_back += 1
        print(f"  {serial:<20} {held:.0f} -> {want}")

    print(f"  {put_back} put back" if put_back else "  nothing had moved")


def units(admin) -> None:
    """Add the units the catalogue is priced in, leaving the rest alone."""

    print("Units")

    held = set(api("get", "/inventory/units", admin).json().get("data") or [])

    for name in UNITS:
        if name in held:
            print(f"  {name} already there")
            continue

        created = api("post", "/inventory/units", admin, json={"name": name})

        if created.status_code >= 400:
            print(f"  {name}: {created.status_code} {created.text[:120]}")
            continue

        print(f"  {name} added")


def product_types(admin) -> dict:
    """Make sure every type exists and sits under a real category group."""

    print("\nProduct types")

    groups = {
        g["name"]
        for g in rows(api("get", "/category-groups/", admin))
    }

    held = {t["code"]: t for t in rows(api("get", "/product-types/", admin))}

    for code, name, category in PRODUCT_TYPES:
        if category not in groups:
            print(f"  {code}: there is no {category!r} category group - skipped")
            continue

        existing = held.get(code)

        if existing is None:
            created = api("post", "/product-types/", admin, json={
                "name": name,
                "code": code,
                "category": category,
                "description": f"{name} stocked for the {category} group.",
            })

            if created.status_code >= 400:
                print(f"  {code}: {created.status_code} {created.text[:120]}")
                continue

            held[code] = created.json()["data"]
            print(f"  {code} created under {category}")
            continue

        if existing.get("category") == category:
            print(f"  {code} already under {category}")
            continue

        # The category it was filed under is not a category group, so the
        # Inventory table had nothing real to print. Put it right.
        was = existing.get("category")

        fixed = api("put", f"/product-types/{existing['id']}", admin, json={
            "name": existing.get("name") or name,
            "code": code,
            "category": category,
            "description": existing.get("description") or "",
        })

        if fixed.status_code >= 400:
            print(f"  {code}: {fixed.status_code} {fixed.text[:120]}")
            continue

        held[code] = fixed.json()["data"]
        print(f"  {code} moved from {was!r} to {category!r}")

    return held


def catalogue(admin, types: dict) -> None:
    """Put the products on the shelf, never touching one already there."""

    print("\nCatalogue")

    companies = {
        c["company_name"]: str(c["id"])
        for c in rows(api("get", "/companies/", admin, params={"size": 200}))
    }

    held = {
        str(item.get("serial_number") or "").upper()
        for item in rows(api("get", "/inventory/items", admin))
    }

    added = skipped = 0

    for code, name, serial, rate, unit, instock, case_size in CATALOGUE + DEMO_KITS:
        if serial.upper() in held:
            skipped += 1
            continue

        product_type = types.get(code)

        if product_type is None:
            print(f"  {serial}: no {code} product type to file it under")
            continue

        body = {
            "name": name,
            "serial_number": serial,
            "product_type_code": code,
            "category": product_type["category"],
            "attributes": {
                "rate": rate,
                "unit": unit,
                "instock": instock,
                "case_size": case_size,
            },
        }

        owner = COMPANY_ONLY.get(serial)

        if owner:
            if owner not in companies:
                print(f"  {serial}: there is no {owner!r} company - skipped")
                continue
            body["company_id"] = companies[owner]

        created = api("post", "/inventory/items", admin, json=body)

        if created.status_code >= 400:
            print(f"  {serial}: {created.status_code} {created.text[:140]}")
            continue

        added += 1
        where = f" for {owner}" if owner else ""
        print(f"  {serial:<20} {instock:>3} {unit:<8} {name}{where}")

    print(f"\n  {added} added, {skipped} already on the shelf")


def realign(admin, types: dict) -> None:
    """Bring a seeded product's details back in line with this file.

    A product copies its type, category, rate and unit at the moment it is
    created, so the three seeded before this catalogue existed were left
    stranded: all three filed under Display Systems at 185,000 a unit, so
    an OPS PC sat among the panels priced like one, and their Category
    Group read "Interactive Panels" - which is not a category group at all,
    and so appeared nowhere in Masters.

    Stock is the one thing never touched here. What is on the shelf is a
    fact about the warehouse, not about this file; ``--restock`` is the
    separate, opt-in way to reset it.
    """

    print("\nBringing seeded products in line")

    intended = {
        serial.upper(): (code, name, rate, unit, case_size)
        for code, name, serial, rate, unit, _, case_size in CATALOGUE + DEMO_KITS
    }

    fixed_count = 0

    for item in rows(api("get", "/inventory/items", admin)):
        serial = str(item.get("serial_number") or "").upper()
        want = intended.get(serial)

        if want is None:
            continue

        code, name, rate, unit, case_size = want
        product_type = types.get(code)

        if product_type is None:
            continue

        attributes = dict(item.get("attributes") or {})

        changes = []

        if str(item.get("product_type_code") or "").upper() != code:
            changes.append(f"type {item.get('product_type_code')} -> {code}")

        if item.get("category") != product_type["category"]:
            changes.append(f"group {item.get('category')!r} -> {product_type['category']!r}")

        if float(attributes.get("rate") or 0) != float(rate):
            changes.append(f"rate {attributes.get('rate')} -> {rate}")

        if attributes.get("unit") != unit:
            changes.append(f"unit {attributes.get('unit')!r} -> {unit!r}")

        if float(attributes.get("case_size") or 0) != float(case_size):
            changes.append(f"case {attributes.get('case_size')} -> {case_size}")

        if not changes:
            continue

        attributes.update({"rate": rate, "unit": unit, "case_size": case_size})

        updated = api("put", f"/inventory/items/{item['_id']}", admin, json={
            "name": name,
            "serial_number": item.get("serial_number"),
            "product_type_code": code,
            "category": product_type["category"],
            # instock comes straight back through, untouched.
            "attributes": attributes,
            "company_id": item.get("company_id"),
        })

        if updated.status_code >= 400:
            print(f"  {serial}: {updated.status_code} {updated.text[:120]}")
            continue

        fixed_count += 1
        print(f"  {serial:<20} {', '.join(changes)}")

    print(f"  {fixed_count} corrected" if fixed_count else "  all in line")


def report(admin) -> None:
    """What the warehouse now holds, the way the desk counts it."""

    items = rows(api("get", "/inventory/items", admin))

    def stock(item) -> float:
        return float((item.get("attributes") or {}).get("instock") or 0)

    out = [i for i in items if stock(i) == 0]
    thin = [i for i in items if 0 < stock(i) <= 3]

    print(
        f"\n  {len(items)} products, "
        f"{int(sum(stock(i) for i in items))} units on hand, "
        f"{len(thin)} running thin, {len(out)} out of stock"
    )

    for item in sorted(out, key=lambda i: i.get("name") or ""):
        print(f"    out    {item.get('serial_number')}  {item.get('name')}")

    for item in sorted(thin, key=lambda i: stock(i)):
        print(f"    {int(stock(item))} left {item.get('serial_number')}  {item.get('name')}")


if __name__ == "__main__":
    sys.exit(main())
