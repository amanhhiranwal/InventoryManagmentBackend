"""A sheet of products is loaded the way the form loads one.

The thing worth proving is the slash. A supplier writes three identical
panels on one line with three serial numbers between slashes, and three
products have to come out of it - each with its own serial, because a
serial number that only exists inside a slash-separated string is one
nobody can scan, search or sell against.

The rest of it is about refusing badly: a sheet with one bad row loads
nothing, and the reply says which row and why, so the person fixing it
does not have to guess what got through.

Everything goes over HTTP, through the route, with the permission check
in front of it - a service that works behind a route that does not is a
500 nobody saw coming.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_inventory_bulk.py
"""

import csv
import io
import sys
import uuid

sys.path.insert(0, "/app")

from seed_sales_team import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    TEAM_PASSWORD,
    api,
    login,
    rows,
)

TAG = uuid.uuid4().hex[:6].upper()

passed, failed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))


HEADERS = [
    "Model Number",
    "Product Name *",
    "Specification",
    "Hardware Serial Number *",
    "Product Type Code *",
    "Company",
    "ECP Rate *",
    "DTP Rate",
    "In-Stock Qty *",
    "Case Size *",
    "Unit *",
    "HSN / SAC Code",
]


def sheet_with(heads, *rows_in, fill=True) -> bytes:
    """Those rows as a .xlsx, laid out like the template: title, then heads.

    Every product needs a model number and a specification - they go on
    the sticker and on the proposal line - so rows that leave them blank
    get stand-ins, and the sections that are about those two fields pass
    fill=False to say what they mean.
    """

    from openpyxl import Workbook

    book = Workbook()
    page = book.active
    page.title = "Products"

    page.append(["Inventory data collection - one product per row"])
    page.append(["Shaded columns are required."])
    page.append(list(heads))

    for row in rows_in:
        row = list(row)

        if fill:
            if not str(row[0] or "").strip():
                row[0] = f"MDL-{TAG}"

            if not str(row[2] or "").strip():
                row[2] = "20-point touch, Android 16"

        page.append(row)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def sheet(*rows_in, fill=True) -> bytes:
    return sheet_with(HEADERS, *rows_in, fill=fill)


def comma_separated(*rows_in) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)

    for row in rows_in:
        row = list(row)

        # The same stand-ins as the Excel helper, for the same reason.
        if not str(row[0] or "").strip():
            row[0] = f"MDL-{TAG}"

        if not str(row[2] or "").strip():
            row[2] = "20-point touch, Android 16"

        writer.writerow(row)

    return buffer.getvalue().encode("utf-8")


def upload(token, content, filename, commit=False):
    return api(
        "post",
        f"/inventory/items/bulk?commit={'true' if commit else 'false'}",
        token,
        files={"file": (filename, content)},
    )


def body(response):
    try:
        return response.json()
    except Exception:  # pragma: no cover - only when the server falls over
        return {"detail": response.text[:200]}


def data(response):
    return body(response).get("data") or {}


made: list[str] = []

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    # ------------------------------------------------------ what exists
    types = rows(api("get", "/product-types", admin))
    units = rows(api("get", "/inventory/units", admin))

    if not types or not units:
        print("Seed the Product Types and Units masters first.")
        raise SystemExit(1)

    code = str(types[0]["code"])
    unit = str(units[0])

    print(f"\nUsing product type {code} and unit {unit}, tag {TAG}")

    print("\n1. The template is the sheet this server actually reads")

    template = api("get", "/inventory/items/bulk/template", admin)

    check("the template downloads", template.status_code == 200, str(template.status_code))
    check(
        "it is an Excel file",
        template.content[:2] == b"PK",
        str(template.content[:8]),
    )

    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(template.content))

    check(
        "it has a Products tab to fill in",
        "Products" in book.sheetnames,
        str(book.sheetnames),
    )
    # Shaded rows are not filled-in rows: what matters is that there is
    # nothing under the headings for the importer to read as a product.
    filled = [
        row
        for row in book["Products"].iter_rows(min_row=2, values_only=True)
        if any(str(value or "").strip() for value in row)
    ]

    check(
        "the examples are on their own tab, not among the products",
        "Example rows" in book.sheetnames and filled == [],
        f"{book.sheetnames} / {len(filled)} rows under the headings",
    )

    heads = [str(c.value or "") for c in book["Products"][1]]

    check(
        "every column the form asks for is on it",
        all(
            any(h.startswith(wanted) for h in heads)
            for wanted in ("Product Name", "Hardware Serial Number", "ECP Rate", "Unit")
        ),
        str(heads),
    )

    # A sheet filled in from the downloaded template, headings and all,
    # is the sheet the next sections upload.
    print("\n2. A good sheet says what it would do, and does nothing yet")

    good = sheet(
        [
            "MDL-1",
            f"Bulk Panel {TAG}",
            "75 inch",
            f"BULK-{TAG}-001",
            code,
            "",
            71000,
            58000,
            4,
            1,
            unit,
            "85285900",
        ],
    )

    preview = upload(admin, good, "products.xlsx")

    check("the preview is accepted", preview.status_code == 200, str(preview.status_code))
    check("it plans one product", data(preview).get("products") == 1, str(data(preview)))
    check("it has not created anything", data(preview).get("created") == 0)
    check("it has no complaints", data(preview).get("errors") == [], str(data(preview).get("errors")))

    listed = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}", admin))
    check("nothing is in the catalogue yet", listed == [], str(len(listed)))

    print("\n3. Committing loads it")

    done = upload(admin, good, "products.xlsx", commit=True)

    check("the upload is accepted", done.status_code == 200, str(done.status_code))
    check("one product was created", data(done).get("created") == 1, str(data(done)))

    listed = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-001", admin))
    made += [row["_id"] for row in listed]

    check("it is in the catalogue", len(listed) == 1, str(len(listed)))

    if listed:
        saved = listed[0]
        attributes = saved.get("attributes") or {}

        check("under the name on the sheet", saved["name"] == f"Bulk Panel {TAG}")
        check("with its serial number", saved["serial_number"] == f"BULK-{TAG}-001")
        check("filed under the product type", saved["product_type_code"] == code)
        check("the ECP rate came across", float(attributes.get("rate") or 0) == 71000)
        check("the DTP rate came across", float(attributes.get("dtp_rate") or 0) == 58000)
        check("the stock figure came across", float(attributes.get("instock") or 0) == 4)
        check("the unit came across", attributes.get("unit") == unit)
        check("the HSN code came across", str(attributes.get("hsn_code")) == "85285900")
        check("the model number came across", attributes.get("model_number") == "MDL-1")

    print("\n4. One row, three serial numbers, three products")

    three = sheet(
        [
            "MDL-3",
            f"Bulk Trio {TAG}",
            "",
            f"BULK-{TAG}-A1/BULK-{TAG}-A2 / BULK-{TAG}-A3",
            code,
            "",
            5000,
            "",
            1,
            1,
            unit,
            "",
        ],
    )

    trio = upload(admin, three, "products.xlsx", commit=True)

    check("the sheet is accepted", trio.status_code == 200, str(body(trio)))
    check("three products came out of one row", data(trio).get("created") == 3, str(data(trio).get("created")))

    saved = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-A", admin))
    made += [row["_id"] for row in saved]

    serials = sorted(row["serial_number"] for row in saved)

    check(
        "each serial number is its own record",
        serials == [f"BULK-{TAG}-A1", f"BULK-{TAG}-A2", f"BULK-{TAG}-A3"],
        str(serials),
    )
    check(
        "and no record is left holding the slashes",
        all("/" not in row["serial_number"] for row in saved),
        str(serials),
    )
    check(
        "they all carry the rest of the row",
        all(row["name"] == f"Bulk Trio {TAG}" for row in saved)
        and all(float((row.get("attributes") or {}).get("rate") or 0) == 5000 for row in saved),
    )
    check(
        "each holds one unit, not the row's figure three times over",
        all(
            float((row.get("attributes") or {}).get("instock") or 0) == 1
            for row in saved
        ),
        str([(row.get("attributes") or {}).get("instock") for row in saved]),
    )

    print("\n4b. A delivery written the way the warehouse writes it")

    # Seventy-five panels on one line, a line break after each slash, and
    # In-Stock Qty saying 75 - meaning the delivery, not 75 of each. The
    # sheet this is modelled on came back from the inventory team like
    # this, and the first rule I wrote would have made 5,625 units of it.
    many = [f"BULK-{TAG}-D{n:03d}" for n in range(1, 76)]

    delivery = sheet(
        [
            '65" SPX-3576',
            f"Bulk Delivery {TAG}",
            '65" 4K UHD, 20-point touch',
            "/\n".join(many),
            code,
            "",
            75000,
            63000,
            75,
            1,
            unit,
            "",
        ],
    )

    loaded = upload(admin, delivery, "products.xlsx", commit=True)

    check(
        "seventy-five serial numbers become seventy-five products",
        data(loaded).get("created") == 75,
        str(data(loaded).get("created") or body(loaded)),
    )

    saved = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-D", admin))
    made += [row["_id"] for row in saved]

    stock = [float((row.get("attributes") or {}).get("instock") or 0) for row in saved]

    check(
        "and the delivery is seventy-five units, not five thousand",
        sum(stock) == 75,
        f"{sum(stock)} units across {len(saved)} products",
    )
    check(
        "the line breaks did not end up in a serial number",
        all(
            row["serial_number"].strip() == row["serial_number"]
            and "\n" not in row["serial_number"]
            for row in saved
        ),
        str([r["serial_number"] for r in saved][:2]),
    )

    # The other way the same delivery gets written down.
    one_each = sheet(
        [
            "",
            f"Bulk Each {TAG}",
            "",
            f"BULK-{TAG}-E1/BULK-{TAG}-E2",
            code,
            "",
            100,
            "",
            1,
            1,
            unit,
            "",
        ],
    )

    either = upload(admin, one_each, "products.xlsx", commit=True)

    check(
        "writing 1 for 'one of each' is read the same way",
        data(either).get("created") == 2,
        str(data(either).get("created") or body(either)),
    )

    saved = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-E", admin))
    made += [row["_id"] for row in saved]

    check(
        "and both hold one unit",
        [float((row.get("attributes") or {}).get("instock") or 0) for row in saved] == [1, 1],
        str([(row.get("attributes") or {}).get("instock") for row in saved]),
    )

    # Anything else is two claims about the same delivery.
    muddled = sheet(
        [
            "",
            f"Bulk Muddle {TAG}",
            "",
            f"BULK-{TAG}-M1/BULK-{TAG}-M2/BULK-{TAG}-M3",
            code,
            "",
            100,
            "",
            40,
            1,
            unit,
            "",
        ],
    )

    refused = upload(admin, muddled, "products.xlsx", commit=True)

    check(
        "three serial numbers and a quantity of 40 is refused",
        data(refused).get("created") == 0,
        str(data(refused)),
    )
    check(
        "and the refusal says what to write instead",
        any(
            "3 serial numbers" in e and "In-Stock Qty says 40" in e
            for e in data(refused).get("errors", [])
        ),
        str(data(refused).get("errors")),
    )

    print("\n4c. What cover costs, a column per term")

    terms = rows(api("get", "/warranty-terms", admin))
    term_name = str((terms[0] or {}).get("name") or "") if terms else ""

    if not term_name:
        check("there is a warranty term to price", False, "none in Masters")
    else:
        with_cover = sheet_with(
            HEADERS + [f"{term_name} Warranty Rate", f"{term_name} Rate Mode"],
            [
                "",
                f"Bulk Cover {TAG}",
                "",
                f"BULK-{TAG}-W1",
                code,
                "",
                1000,
                "",
                1,
                1,
                unit,
                "",
                7.5,
                "%",
            ],
        )

        covered = upload(admin, with_cover, "products.xlsx", commit=True)

        check(
            "the sheet loads with its warranty columns",
            covered.status_code == 200 and data(covered).get("created") == 1,
            str(body(covered)),
        )

        saved = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-W1", admin))
        made += [row["_id"] for row in saved]

        priced = ((saved[0].get("attributes") if saved else {}) or {}).get(
            "warranty_rates"
        ) or {}

        check(
            f"{term_name} is priced on the product",
            priced.get(term_name, {}).get("rate") == 7.5
            and priced.get(term_name, {}).get("mode") == "PERCENT",
            str(priced),
        )

    print("\n4d. A product with no model or specification cannot be loaded")

    # Both are on the sticker that goes on the box and on the proposal
    # line a customer reads, so a sheet that leaves them out would load
    # products nobody downstream could identify.
    bare = sheet(
        ["", f"Bulk Bare {TAG}", "", f"BULK-{TAG}-B1", code, "", 100, "", 1, 1, unit, ""],
        fill=False,
    )

    refused = upload(admin, bare, "products.xlsx", commit=True)

    check("it is refused", data(refused).get("created") == 0, str(data(refused)))
    check(
        "and names both fields",
        any("Model Number is missing" in e for e in data(refused).get("errors", []))
        and any("Specification is missing" in e for e in data(refused).get("errors", [])),
        str(data(refused).get("errors")),
    )

    print("\n5. A sheet with anything wrong in it loads nothing at all")

    mixed = sheet(
        # Fine.
        ["", f"Bulk Fine {TAG}", "", f"BULK-{TAG}-OK1", code, "", 100, "", 1, 1, unit, ""],
        # No name.
        ["", "", "", f"BULK-{TAG}-BAD", code, "", 100, "", 1, 1, unit, ""],
        # Fine again, so a refusal is not just "it stopped at the first row".
        ["", f"Bulk Fine {TAG}", "", f"BULK-{TAG}-OK2", code, "", 100, "", 1, 1, unit, ""],
    )

    refused = upload(admin, mixed, "products.xlsx", commit=True)

    check("the sheet is refused", data(refused).get("success", body(refused).get("success")) is False)
    check("nothing was created", data(refused).get("created") == 0, str(data(refused).get("created")))
    check(
        "the complaint names the row",
        any("Row 5" in e for e in data(refused).get("errors", [])),
        str(data(refused).get("errors")),
    )

    left = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-OK", admin))
    check("not even the good rows went in", left == [], str(len(left)))

    print("\n6. The same serial number twice is refused")

    twice = sheet(
        ["", f"Bulk Twin {TAG}", "", f"BULK-{TAG}-T1", code, "", 100, "", 1, 1, unit, ""],
        ["", f"Bulk Twin {TAG}", "", f"BULK-{TAG}-T1", code, "", 100, "", 1, 1, unit, ""],
    )

    twin = upload(admin, twice, "products.xlsx", commit=True)

    check("it is refused", data(twin).get("created") == 0)
    check(
        "and says which row claimed it first",
        any("already used on row" in e for e in data(twin).get("errors", [])),
        str(data(twin).get("errors")),
    )

    print("\n7. A serial number already in the catalogue is refused")

    again = sheet(
        ["", f"Bulk Panel {TAG}", "", f"BULK-{TAG}-001", code, "", 100, "", 1, 1, unit, ""],
    )

    repeat = upload(admin, again, "products.xlsx", commit=True)

    check("it is refused", data(repeat).get("created") == 0)
    check(
        "and says it is already in the catalogue",
        any("already in the catalogue" in e for e in data(repeat).get("errors", [])),
        str(data(repeat).get("errors")),
    )

    print("\n8. What is not in Masters cannot be loaded")

    unknown_type = sheet(
        ["", f"Bulk X {TAG}", "", f"BULK-{TAG}-X1", "NOSUCHTYPE", "", 100, "", 1, 1, unit, ""],
    )

    refusal = upload(admin, unknown_type, "products.xlsx", commit=True)

    check("an unknown product type is refused", data(refusal).get("created") == 0)
    check(
        "and the codes that do exist are listed",
        any(code in e and "no product type" in e for e in data(refusal).get("errors", [])),
        str(data(refusal).get("errors")),
    )

    unknown_unit = sheet(
        ["", f"Bulk X {TAG}", "", f"BULK-{TAG}-X2", code, "", 100, "", 1, 1, "Furlongs", ""],
    )

    refusal = upload(admin, unknown_unit, "products.xlsx", commit=True)

    check("an unknown unit is refused", data(refusal).get("created") == 0)
    check(
        "and the units that do exist are listed",
        any("no unit" in e and unit in e for e in data(refusal).get("errors", [])),
        str(data(refusal).get("errors")),
    )

    print("\n9. A figure that is not a figure is refused")

    nonsense = sheet(
        ["", f"Bulk X {TAG}", "", f"BULK-{TAG}-X3", code, "", "about seventy", "", 1, 1, unit, ""],
    )

    refusal = upload(admin, nonsense, "products.xlsx", commit=True)

    check("it is refused", data(refusal).get("created") == 0)
    check(
        "and says which figure",
        any("ECP Rate" in e for e in data(refusal).get("errors", [])),
        str(data(refusal).get("errors")),
    )

    print("\n10. CSV is read the same way as Excel")

    as_csv = comma_separated(
        [
            "MDL-C",
            f"Bulk CSV {TAG}",
            "",
            f"BULK-{TAG}-C1/BULK-{TAG}-C2",
            code,
            "",
            "1,250",
            "",
            2,
            1,
            unit,
            "",
        ],
    )

    loaded = upload(admin, as_csv, "products.csv", commit=True)

    check("the CSV is accepted", loaded.status_code == 200, str(body(loaded)))
    check("both serial numbers became products", data(loaded).get("created") == 2, str(data(loaded)))

    saved = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}-C", admin))
    made += [row["_id"] for row in saved]

    check(
        "a rate typed with a comma is read as a number",
        saved and all(float((row.get("attributes") or {}).get("rate") or 0) == 1250 for row in saved),
        str([(row.get("attributes") or {}).get("rate") for row in saved]),
    )

    print("\n11. Something that is not a sheet is refused politely")

    junk = upload(admin, b"this is not a spreadsheet", "notes.txt", commit=True)

    check("it is refused", junk.status_code == 400, str(junk.status_code))
    check(
        "and says what to upload instead",
        "csv" in str(body(junk).get("detail", "")).lower(),
        str(body(junk).get("detail")),
    )

    headings_only = comma_separated()

    empty = upload(admin, headings_only, "products.csv", commit=True)

    check("a sheet with no rows under the headings is refused", empty.status_code == 400, str(empty.status_code))

    print("\n12. It is a permission, not something everybody has")

    try:
        floor = login("am.north.1@mailinator.com", TEAM_PASSWORD)

        blocked = upload(floor, good, "products.xlsx", commit=True)

        check(
            "an area manager cannot load a sheet",
            blocked.status_code == 403,
            str(blocked.status_code),
        )

        blocked_template = api("get", "/inventory/items/bulk/template", floor)

        check(
            "nor download the template",
            blocked_template.status_code == 403,
            str(blocked_template.status_code),
        )
    except Exception as caught:
        check("an area manager cannot load a sheet", False, str(caught)[:80])

finally:
    # Everything this suite put in the catalogue comes back out, so the
    # Inventory screen looks the same afterwards as before.
    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

        for item_id in set(made):
            api("delete", f"/inventory/items/{item_id}", admin)

        leftover = rows(api("get", f"/inventory/items?grouped=false&search=BULK-{TAG}", admin))

        for row in leftover:
            api("delete", f"/inventory/items/{row['_id']}", admin)
    except Exception as caught:  # pragma: no cover
        print("cleanup:", caught)

print(f"\n{len(passed)} passed, {len(failed)} failed")

if failed:
    for name in failed:
        print("  -", name)

sys.exit(1 if failed else 0)
