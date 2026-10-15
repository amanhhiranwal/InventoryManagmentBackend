"""The sticker on the box, and the two fields that make it worth reading.

A label is only useful if it is the same label. The code on the box has
to be the code on the screen, this week and next, or a warehouse learns
to ignore both and walk back to a desk. So the whole thing is drawn from
the product's own record: same product, same bytes.

What it carries matters as much as that it scans. A sticker with a code
and nothing else means opening a screen to find out what is in the box,
which is the thing it was supposed to save. So the model and the
specification are on it - and because they are, they are required on the
product.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_product_label.py
"""

import re
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


def words(svg: str) -> list[str]:
    """The text drawn on the label, as a reader of it would see it.

    Markup stripped and entities turned back, so a product whose name has
    a quotation mark in it - every panel measured in inches - is compared
    against what is printed rather than against &quot;.
    """

    from html import unescape

    return [
        unescape(re.sub(r"<[^>]+>", "", block)).strip()
        for block in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)
    ]


MODEL = f'65" SPX-{TAG}'
SPEC = '65" 4K UHD, 20-point touch, Android 16, 8GB / 128GB'

made = []

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    types = rows(api("get", "/product-types", admin))
    units = rows(api("get", "/inventory/units", admin))

    if not types or not units:
        print("Seed the Product Types and Units masters first.")
        raise SystemExit(1)

    kind = types[0]

    print("\n1. A product cannot be saved without a model and a specification")

    def create(**overrides):
        payload = {
            "name": f"Label Panel {TAG}",
            "serial_number": f"LABEL-{TAG}-1",
            "product_type_code": kind["code"],
            "category": kind["category"],
            "attributes": {
                "model_number": MODEL,
                "specification": SPEC,
                "rate": 75000,
                "dtp_rate": 63000,
                "unit": str(units[0]),
                "instock": 1,
                "case_size": 1,
                "hsn_code": "85285900",
                **overrides.pop("attributes", {}),
            },
            **overrides,
        }

        return api("post", "/inventory/items", admin, json=payload)

    without_model = create(attributes={"model_number": ""})

    check(
        "no model number is refused",
        without_model.status_code == 400,
        f"{without_model.status_code} {without_model.text[:120]}",
    )
    check(
        "and says which field",
        "Model Number" in without_model.text,
        without_model.text[:120],
    )

    without_spec = create(attributes={"specification": "   "})

    check(
        "no specification is refused",
        without_spec.status_code == 400,
        f"{without_spec.status_code} {without_spec.text[:120]}",
    )

    made_ok = create()

    check(
        "with both, it saves",
        made_ok.status_code == 200,
        f"{made_ok.status_code} {made_ok.text[:160]}",
    )

    item_id = (made_ok.json().get("data") or {}).get("_id")

    if item_id:
        made.append(item_id)

    print("\n2. The label says what is in the box")

    label = api("get", f"/inventory/items/{item_id}/label", admin)

    check("the label is drawn", label.status_code == 200, str(label.status_code))
    check(
        "as SVG, so it prints at any size",
        label.text.lstrip().startswith("<?xml") or "<svg" in label.text[:400],
        label.text[:80],
    )

    printed = words(label.text)

    check(
        "the product's name is on it",
        any(f"Label Panel {TAG}" in line for line in printed),
        str(printed),
    )
    check(
        "the model number is on it",
        any(MODEL in line for line in printed),
        str(printed),
    )
    check(
        "the specification is on it",
        any("4K UHD" in line for line in printed),
        str(printed),
    )
    check(
        "the HSN is on it, because the invoice needs it",
        any("85285900" in line for line in printed),
        str(printed),
    )
    check(
        "and the serial number the barcode carries",
        any(f"LABEL-{TAG}-1" in line for line in printed),
        str(printed),
    )

    print("\n3. The same product gives the same label, every time")

    again = api("get", f"/inventory/items/{item_id}/label", admin)

    check(
        "two requests give byte-for-byte the same label",
        again.text == label.text,
        f"{len(label.text)} vs {len(again.text)} bytes",
    )

    # The screen and the sticker are the same markup, so there is nothing
    # that could drift between them.
    from app.services.product_label_service import label_svg  # noqa: E402

    twice = [
        label_svg(
            sku=f"LABEL-{TAG}-1",
            name=f"Label Panel {TAG}",
            hsn="85285900",
            model=MODEL,
            specification=SPEC,
            unit=str(units[0]),
            url="https://example.invalid/inventory?product=X",
        )
        for _ in range(2)
    ]

    check("drawn twice, identical", twice[0] == twice[1], str(len(twice[0])))

    print("\n4. The QR carries where to look the product up")

    # The QR is drawn as paths, so it cannot be read back here. What can
    # be shown is that its content comes from the link: change the link
    # and the drawing changes, leave it out and it changes again.
    other_link = label_svg(
        sku=f"LABEL-{TAG}-1",
        name=f"Label Panel {TAG}",
        hsn="85285900",
        model=MODEL,
        specification=SPEC,
        unit=str(units[0]),
        url="https://example.invalid/inventory?product=Y",
    )

    no_link = label_svg(
        sku=f"LABEL-{TAG}-1",
        name=f"Label Panel {TAG}",
        hsn="85285900",
        model=MODEL,
        specification=SPEC,
        unit=str(units[0]),
    )

    check(
        "a different product makes a different QR",
        other_link != twice[0],
    )
    check(
        "and with no address to send anyone to, it still draws one",
        no_link != twice[0] and "<svg" in no_link[:400],
    )

    print("\n5. A label is only drawn for a product you may see")

    try:
        floor = login("am.north.1@mailinator.com", TEAM_PASSWORD)

        # The serial it was filed under belongs to every company, so what
        # is being checked is the permission, not the company scope.
        theirs = api("get", f"/inventory/items/{item_id}/label", floor)

        check(
            "somebody with inventory.read can print one",
            theirs.status_code == 200,
            str(theirs.status_code),
        )
    except Exception as caught:
        check("somebody with inventory.read can print one", False, str(caught)[:80])

    missing = api(
        "get", "/inventory/items/000000000000000000000000/label", admin
    )

    check(
        "a product that does not exist has no label",
        missing.status_code == 404,
        str(missing.status_code),
    )

finally:
    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

        for item_id in set(made):
            api("delete", f"/inventory/items/{item_id}", admin)

        for row in rows(api("get", f"/inventory/items?search=LABEL-{TAG}", admin)):
            api("delete", f"/inventory/items/{row['_id']}", admin)
    except Exception as caught:  # pragma: no cover
        print("cleanup:", caught)

print(f"\n{len(passed)} passed, {len(failed)} failed")

for name in failed:
    print("  -", name)

sys.exit(1 if failed else 0)
