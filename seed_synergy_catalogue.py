"""The real Synergy catalogue, in place of the demo one.

Built from the Noida 65 stock dashboard: the interactive panels as they
are actually stocked - a board type crossed with a size, with or without a
camera - plus the OPS modules, standees, cameras and microphones that go
out alongside them.

Reading the dashboard:

    CPX  carries a camera, SPX does not
    6 = 65in, 7 = 75in, 8 = 86in, 9 = 98in, 11 = 110in
    the board type decides the SoC, the memory and the Android version

Only the combinations the dashboard actually shows stock against are put
up. The grid has forty-five cells and twelve of them are stocked; seeding
the empty thirty-three would be a catalogue of things nobody sells.

HSN codes are the ones written on the dashboard by hand, not guessed.

    docker exec -w /app backend_app python seed_synergy_catalogue.py
    docker exec -w /app backend_app python seed_synergy_catalogue.py --replace

--replace also retires the demo catalogue that came before it. Without it
the demo lines are left alone, so a half-finished demo order still has its
products.
"""

import sys

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, api, login, rows

#: Board type -> what it is, for the description a client reads.
BOARDS = {
    "CVT9679":  ("CVT 9679",   "9679 CVTE, 8GB RAM / 128GB ROM, Android 14"),
    "CVT311D2": ("CVT 311D2",  "311D2 CVTE, 8GB RAM / 128GB ROM, Android 14"),
    "LANGOV100": ("LangoV100", "Lango V100, 8GB RAM / 128GB ROM, Android 14"),
    "LANGO3576": ("Lango 3576", "Lango RK3576, 8GB RAM / 128GB ROM, Android 16"),
    "YS3576":   ("YS 3576",    "YS RK3576, 8GB RAM / 128GB ROM, Android 16"),
}

#: Model -> (size, carries a camera, HSN as written on the dashboard).
MODELS = {
    "SPX6":  ('65"',  False, "85285900"),
    "CPX6":  ('65"',  True,  "85285900"),
    "SPX7":  ('75"',  False, "84714190"),
    "CPX7":  ('75"',  True,  "84714190"),
    "SPX8":  ('86"',  False, "85285900"),
    "CPX8":  ('86"',  True,  "85285900"),
    "SPX9":  ('98"',  False, "85285900"),
    "CPX9":  ('98"',  True,  "85285900"),
    "CPX11": ('110"', True,  "85285900"),
}

#: (board, model, units on the shelf), straight off the dashboard.
PANELS = [
    ("CVT9679",   "SPX7",  68),
    ("CVT9679",   "CPX7",   6),
    ("CVT311D2",  "CPX11",  3),
    ("LANGOV100", "SPX6",  14),
    ("LANGOV100", "SPX7",  74),
    ("LANGOV100", "SPX8",  40),
    ("LANGOV100", "CPX8",   3),
    ("LANGOV100", "CPX9",   6),
    ("LANGO3576", "SPX6", 119),
    ("LANGO3576", "SPX7", 120),
    ("YS3576",    "CPX6", 118),
    ("YS3576",    "CPX7",  12),
]

#: Rate per panel, by the dashboard's own column.
#:
#: Taken from the LangoV100 row, which is the only row priced on the sheet
#: and therefore reads as the price list for the column rather than for
#: that board alone. A camera variant is dearer than its SPX twin by the
#: sheet's own figures, so no premium is added on top - the number written
#: against CPX already carries it.
#:
#: SPX9 and CPX11 are both written at 150000. CPX9 is not written at all;
#: it is given the 98" figure beside it rather than left unpriced, and is
#: the one number here that is inferred rather than read.
RATE_BY_MODEL = {
    "SPX6":   68000,
    "CPX6":   70000,
    "SPX7":   75000,
    "CPX7":   77000,
    "SPX8":   85000,
    "CPX8":   88000,
    "SPX9":  150000,
    "CPX9":  150000,
    "CPX11": 150000,
}

#: type code, name, SKU, rate, unit, stock, case size, HSN
#:
#: Every line is priced. The seven the stock dashboard named without a
#: figure - the three cameras, the array mic, the panel stand and the two
#: non-assembled OPS - were carried at zero until the rates came through,
#: rather than being guessed at: an invented figure on a customer's
#: quotation is worse than a visible blank.
EXTRAS = [
    # OPS compute modules, by processor, memory and generation. 85291029,
    # as written on the dashboard.
    ("COMPUTE", "OPS i5 8GB/256GB 10th Gen",     "SG-OPS-I5-8-256-G10",  22000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i5 8GB/256GB 11th Gen",     "SG-OPS-I5-8-256-G11",  23000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i5 8GB/256GB 12th Gen",     "SG-OPS-I5-8-256-G12",  23500, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i5 8GB/512GB 10th Gen",     "SG-OPS-I5-8-512-G10",  26000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i5 8GB/512GB 11th Gen",     "SG-OPS-I5-8-512-G11",  26500, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i5 8GB/512GB 12th Gen",     "SG-OPS-I5-8-512-G12",  32000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i7 8GB/256GB 10th Gen",     "SG-OPS-I7-8-256-G10",  28000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i7 8GB/256GB 11th Gen",     "SG-OPS-I7-8-256-G11",  30000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i7 8GB/512GB 10th Gen",     "SG-OPS-I7-8-512-G10",  35000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i7 8GB/512GB 11th Gen",     "SG-OPS-I7-8-512-G11",  40000, "Nos",  0, 1, "85291029"),
    ("COMPUTE", "OPS i5 Non Assembled",          "SG-OPS-I5-NA",         30000, "Nos", 11, 1, "85291029"),
    ("COMPUTE", "OPS i7 Non Assembled",          "SG-OPS-I7-NA",         30000, "Nos", 15, 1, "85291029"),

    # Standees. One price for touch and one for non-touch, as the sheet
    # prices them - by the panel inside, not by the cabinet size.
    ("INFRA", "Standee Touch",     "SG-STD-TOUCH",    60000, "Nos", 0, 1, "85285900"),
    ("INFRA", "Standee Non-Touch", "SG-STD-NONTOUCH", 57000, "Nos", 6, 1, "85285900"),

    # Cameras and microphones. Named on the sheet, none of them priced.
    ("AUDIO", "UHD All in One USB Video Bar 12V 5A",        "SG-CAM-UHDBAR",   1949, "Nos", 4, 1, "85258900"),
    ("AUDIO", "Camera 360 Degree",                           "SG-CAM-360",      2500, "Nos", 4, 1, "85258900"),
    ("AUDIO", "4K Business Webcam HF0V-120 Degree",          "SG-CAM-4KHF0V",   3000, "Nos", 6, 1, "85258900"),
    ("AUDIO", "Cascading Omnidirectional Digital Array Mic", "SG-MIC-CASCADE", 50000, "Nos", 1, 1, "85184000"),

    # The mounting hardware the challan bills alongside a panel.
    ("INFRA", "Panel Stand", "SG-STAND-PANEL", 11000, "Nos", 0, 1, "84733099"),
]

#: Demo lines retired by --replace. Named rather than "everything else",
#: so a product somebody added by hand is never swept up with them.
DEMO_SERIAL_PREFIXES = ("NX-",)

#: Lines this revision renames or re-cuts, retired by --replace so the
#: catalogue does not carry both spellings of the same product.
SUPERSEDED_SKUS = (
    "SG-OPS-I5-8-256-G12", "SG-OPS-I5-8-512-G12",
    "SG-OPS-I7-8-256-G13", "SG-OPS-I7-8-512-G12",
    "SG-OPS-I5-NA-G12", "SG-OPS-I7-NA-G13",
    "SG-STD-43-NT", "SG-STD-49-NT", "SG-STD-55-T", "SG-STD-65-T",
    "SG-STAND-IFP",
)

#: Two lines filed under one company each, so per-company products can be
#: seen being kept apart - a region's demo panel is not something the
#: other region can quote. Real catalogue lines rather than leftovers from
#: the demo set, so retiring that set does not take the test with it.
COMPANY_ONLY = {
    "SG-DEMO-NORTH": "Synergy North Agro",
    "SG-DEMO-SOUTH": "Synergy South Seeds",
}

COMPANY_LINES = [
    ("DISPLAY", 'North Region 75" Demo Panel', "SG-DEMO-NORTH", 75000, "Nos", 2, 1, "85285900"),
    ("DISPLAY", 'South Region 75" Demo Panel', "SG-DEMO-SOUTH", 75000, "Nos", 2, 1, "85285900"),
]


def panel_rows():
    """The panel matrix as catalogue lines."""

    for board, model, stock in PANELS:
        board_name, board_spec = BOARDS[board]
        size, has_camera, hsn = MODELS[model]

        rate = RATE_BY_MODEL[model]

        name = f'{size} Interactive Flat Panel {model} ({board_name})'
        description = board_spec + (", with camera and array mic" if has_camera else "")

        yield ("DISPLAY", name, f"SG-{model}-{board}", rate, "Nos", stock, 1, hsn, description)


def main() -> int:
    replace = "--replace" in sys.argv

    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    existing = {
        str(item.get("serial_number") or "").upper(): item
        for item in rows(api("get", "/inventory/items", token))
    }

    added = updated = retired = 0

    companies = {
        str(row.get("company_name") or ""): row.get("id")
        for row in rows(api("get", "/inventory/companies", token))
    }

    lines = list(panel_rows()) + [
        (code, name, sku, rate, unit, stock, case, hsn, "")
        for code, name, sku, rate, unit, stock, case, hsn in EXTRAS + COMPANY_LINES
    ]

    for code, name, sku, rate, unit, stock, case, hsn, description in lines:
        attributes = {
            "rate": rate, "rate_per_unit": rate, "unit": unit,
            "instock": stock, "stock": stock, "case_size": case,
            "hsn_code": hsn,
        }

        if description:
            attributes["description"] = description

        payload = {
            "name": name,
            "serial_number": sku,
            "product_type_code": code,
            "category": "Hardware Solutions",
            "attributes": attributes,
        }

        owner = COMPANY_ONLY.get(sku)

        if owner:
            if owner not in companies:
                print(f"  {sku}: there is no {owner!r} company - skipped")
                continue
            payload["company_id"] = companies[owner]

        current = existing.get(sku.upper())

        if current is None:
            r = api("post", "/inventory/items", token, json=payload)
            if r.status_code < 400:
                added += 1
            else:
                print(f"  could not add {sku}: {r.status_code} {r.text[:120]}")
        else:
            # Never overwrite a count somebody has since corrected.
            held = (current.get("attributes") or {}).get("instock")
            if held is not None:
                payload["attributes"]["instock"] = held
                payload["attributes"]["stock"] = held

            r = api("put", f"/inventory/items/{current['_id']}", token, json=payload)
            if r.status_code < 400:
                updated += 1

    if replace:
        #: Never a line this run just wrote. A superseded SKU that is also
        #: a current one would otherwise be added and then deleted in the
        #: same pass, which is how a catalogue ends up emptier than the
        #: run that was meant to fill it.
        current_skus = {sku.upper() for _, _, sku, *_ in lines}

        doomed = set(SUPERSEDED_SKUS) - current_skus

        for serial, item in existing.items():
            if serial in current_skus:
                continue

            if serial.startswith(DEMO_SERIAL_PREFIXES) or serial in doomed:
                r = api("delete", f"/inventory/items/{item['_id']}", token)
                if r.status_code < 400:
                    retired += 1
                else:
                    print(f"  could not retire {serial}: {r.status_code}")

    print(f"\n  {added} added, {updated} updated, {retired} old lines retired")
    print(f"  {len(lines)} products in the Synergy catalogue")

    return 0


if __name__ == "__main__":
    sys.exit(main())
