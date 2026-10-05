"""The Synergy catalogue, from the pricing workbook.

Three sheets, one list:

    END Customer Price  -> the ECP each line is quoted at
    Dealer Price        -> the fixed DTP the channel is bought through at
    Inventory           -> what is on the shelf at Noida 65

A price of 0 means the workbook does not carry one for that line, not that
it is free. Four dealer cells are corrupt in the source file itself -
"760ss0", "9S0ss0", "88888", "*8  8" - and are left unset rather than
guessed at; the pickers show those lines as "Price not set" in amber and
fall back to the end customer rate.

    docker exec -w /app backend_app python seed_synergy_catalogue.py
    docker exec -w /app backend_app python seed_synergy_catalogue.py --replace

--replace retires anything left from an earlier cut of the catalogue.
"""

import sys

from seed_sales_team import ADMIN_EMAIL, ADMIN_PASSWORD, api, login, rows

#: type code, name, SKU, ECP, DTP, unit, stock, case size, HSN, spec
CATALOGUE = [
    ('DISPLAY', '65" Interactive Flat Panel SPX (Lango V100)', 'SG-IFP-65-SPX-V100',
     70000, 61000, "Nos", 14, 1, '85285900',
     'Eight-core 1.2 GHz, V100 – A14/8/128, 3 year warranty'),
    ('DISPLAY', '65" Interactive Flat Panel CPX (Lango V100)', 'SG-IFP-65-CPX-V100',
     75000, 65500, "Nos", 0, 1, '85285900',
     'Eight-core 1.2 GHz, V100 – A14/8/128, with camera, 3 year warranty'),
    ('DISPLAY', '75" Interactive Flat Panel SPX (Lango V100)', 'SG-IFP-75-SPX-V100',
     82000, 71000, "Nos", 74, 1, '84714190',
     'Eight-core 1.2 GHz, V100 – A14/8/128, 3 year warranty'),
    ('DISPLAY', '75" Interactive Flat Panel CPX (Lango V100)', 'SG-IFP-75-CPX-V100',
     86000, 0, "Nos", 0, 1, '84714190',
     'Eight-core 1.2 GHz, V100 – A14/8/128, with camera, 3 year warranty'),
    ('DISPLAY', '86" Interactive Flat Panel SPX (Lango V100)', 'SG-IFP-86-SPX-V100',
     110000, 90000, "Nos", 40, 1, '85285900',
     'Eight-core 1.2 GHz, V100 – A14/8/128, 3 year warranty'),
    ('DISPLAY', '86" Interactive Flat Panel CPX (Lango V100)', 'SG-IFP-86-CPX-V100',
     0, 0, "Nos", 3, 1, '85285900',
     'Eight-core 1.2 GHz, V10D – A16/8/128, with camera, 3 year warranty'),
    ('DISPLAY', '65" Interactive Flat Panel SPX EDLA (Lango 3576)', 'SG-IFP-65-SPX-EDLA',
     73000, 63000, "Nos", 119, 1, '85285900',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, EDLA, 3 year warranty'),
    ('DISPLAY', '65" Interactive Flat Panel CPX EDLA NFC (YS 3576)', 'SG-IFP-65-CPX-EDLA',
     77000, 0, "Nos", 119, 1, '85285900',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, EDLA with NFC and camera, 3 year warranty'),
    ('DISPLAY', '75" Interactive Flat Panel SPX (Lango 3576)', 'SG-IFP-75-SPX-3576',
     85000, 0, "Nos", 120, 1, '84714190',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, 3 year warranty'),
    ('DISPLAY', '75" Interactive Flat Panel SPX EDLA (Lango 3576)', 'SG-IFP-75-SPX-EDLA',
     88000, 0, "Nos", 0, 1, '84714190',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, EDLA, 3 year warranty'),
    ('DISPLAY', '75" Interactive Flat Panel CPX EDLA NFC (YS 3576)', 'SG-IFP-75-CPX-EDLA',
     92000, 78000, "Nos", 12, 1, '84714190',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, EDLA with NFC and camera, 3 year warranty'),
    ('DISPLAY', '86" Interactive Flat Panel SPX (3576)', 'SG-IFP-86-SPX-3576',
     0, 93500, "Nos", 0, 1, '85285900',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, 3 year warranty'),
    ('DISPLAY', '86" Interactive Flat Panel CPX NFC (3576)', 'SG-IFP-86-CPX-3576',
     0, 97000, "Nos", 0, 1, '85285900',
     'Eight-core 2.4 GHz, 3576 – A16/8/128, with NFC and camera, 3 year warranty'),
    ('DISPLAY', '98" Interactive Flat Panel CPX (CVTE 311D2)', 'SG-IFP-98-CPX',
     300000, 250000, "Nos", 0, 1, '85285900',
     'Eight-core 2.4 GHz, 311D2 – A14/8/128, with camera, 3 year warranty'),
    ('DISPLAY', '110" Interactive Flat Panel CPX (CVTE 311D2)', 'SG-IFP-110-CPX',
     550000, 430000, "Nos", 3, 1, '85285900',
     'Eight-core 2.4 GHz, 311D2 – A14/8/128, with camera, 3 year warranty'),
    ('COMPUTE', 'OPS i5 12th Gen 8GB/256GB', 'SG-OPS-I5-12G',
     30000, 30000, "Nos", 0, 1, '85291029',
     'Open Pluggable Specification module, Intel i5 12th Gen, 8GB RAM, 256GB'),
    ('COMPUTE', 'OPS i7 13th Gen 8GB/256GB', 'SG-OPS-I7-13G',
     36000, 36000, "Nos", 0, 1, '85291029',
     'Open Pluggable Specification module, Intel i7 13th Gen, 8GB RAM, 256GB'),
    ('COMPUTE', 'OPS i5 Non Assembled', 'SG-OPS-I5-NA',
     30000, 0, "Nos", 11, 1, '85291029',
     ''),
    ('COMPUTE', 'OPS i7 Non Assembled', 'SG-OPS-I7-NA',
     30000, 0, "Nos", 15, 1, '85291029',
     ''),
    ('COMPUTE', 'OPS Upgrade – 16GB RAM', 'SG-OPS-RAM-16',
     6500, 0, "Nos", 0, 1, '84733099',
     ''),
    ('COMPUTE', 'OPS Upgrade – 256GB SSD', 'SG-OPS-SSD-256',
     4500, 0, "Nos", 0, 1, '84733099',
     ''),
    ('COMPUTE', 'OPS Upgrade – 512GB SSD', 'SG-OPS-SSD-512',
     7000, 0, "Nos", 0, 1, '84733099',
     ''),
    ('COMPUTE', 'OPS Upgrade – 1TB SSD', 'SG-OPS-SSD-1TB',
     14000, 0, "Nos", 0, 1, '84733099',
     ''),
    ('COMPUTE', 'OPS Upgrade – 2TB SSD', 'SG-OPS-SSD-2TB',
     24000, 0, "Nos", 0, 1, '84733099',
     ''),
    ('INFRA', 'Standee – Touch', 'SG-STD-TOUCH',
     60000, 0, "Nos", 0, 1, '85285900',
     'Touch standee cabinet'),
    ('INFRA', 'Standee – Non-Touch', 'SG-STD-NONTOUCH',
     57000, 0, "Nos", 6, 1, '85285900',
     'Non-touch standee cabinet; 43″ and 49″ in stock'),
    ('AUDIO', 'UHD All in One USB Video Bar 12V 5A', 'SG-CAM-UHDBAR',
     1949, 0, "Nos", 4, 1, '85258900',
     ''),
    ('AUDIO', 'Camera 360 Degree', 'SG-CAM-360',
     2500, 0, "Nos", 4, 1, '85258900',
     ''),
    ('AUDIO', '4K Business Webcam HF0V-120 Degree', 'SG-CAM-4KHF0V',
     3000, 0, "Nos", 6, 1, '85258900',
     ''),
    ('AUDIO', 'Cascading Omnidirectional Digital Array Mic', 'SG-MIC-CASCADE',
     50000, 0, "Nos", 1, 1, '85184000',
     ''),
    ('SERVICE', 'Extended Warranty 2 Years – 65″ (at purchase)', 'SG-AMC-2Y-65',
     5000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'Extended Warranty 2 Years – 75″ (at purchase)', 'SG-AMC-2Y-75',
     7000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'Extended Warranty 2 Years – 86″ (at purchase)', 'SG-AMC-2Y-86',
     8000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'Extended Warranty 2 Years – 98″ (at purchase)', 'SG-AMC-2Y-98',
     15000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'AMC after 3 Years – 65″', 'SG-AMC-P-65',
     8000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'AMC after 3 Years – 75″', 'SG-AMC-P-75',
     8000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'AMC after 3 Years – 86″', 'SG-AMC-P-86',
     10000, 0, "Nos", 0, 1, '998719',
     ''),
    ('SERVICE', 'AMC after 3 Years – 98″', 'SG-AMC-P-98',
     120000, 0, "Nos", 0, 1, '998719',
     ''),
    ('INFRA', 'UPS', 'SG-UPS',
     5000, 0, "Nos", 0, 1, '85044090',
     ''),
    ('INFRA', 'UPS Cabinet', 'SG-UPS-CABINET',
     900, 0, "Nos", 0, 1, '85044090',
     ''),
    ('INFRA', 'Frame – 65″ & 75″', 'SG-FRAME-65-75',
     22000, 0, "Nos", 0, 1, '84733099',
     ''),
    ('INFRA', 'Frame – 86″', 'SG-FRAME-86',
     25000, 0, "Nos", 0, 1, '84733099',
     ''),
    ('INFRA', 'Panel Stand', 'SG-STAND-PANEL',
     11000, 0, "Nos", 0, 1, '84733099',
     ''),
    ('DISPLAY', 'North Region 75" Demo Panel', 'SG-DEMO-NORTH',
     82000, 71000, "Nos", 2, 1, '84714190',
     'Demonstration unit filed under Synergy North Agro'),
    ('DISPLAY', 'South Region 75" Demo Panel', 'SG-DEMO-SOUTH',
     82000, 71000, "Nos", 2, 1, '84714190',
     'Demonstration unit filed under Synergy South Seeds'),
]

#: Prefixes from catalogue revisions before this one. --replace retires
#: them, so the shelf does not carry two spellings of the same product.
RETIRED_PREFIXES = ("NX-", "SG-SPX", "SG-CPX", "SG-OPS-I5-8-", "SG-OPS-I7-8-",
                    "SG-STD-43", "SG-STD-49", "SG-STD-55", "SG-STD-65")


#: SKU -> the company it belongs to. Everything else is shared.
COMPANY_ONLY = {
    "SG-DEMO-NORTH": "Synergy North Agro",
    "SG-DEMO-SOUTH": "Synergy South Seeds",
}


def main() -> int:
    replace = "--replace" in sys.argv

    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    companies = {
        str(row.get("company_name") or ""): row.get("id")
        for row in rows(api("get", "/inventory/companies", token))
    }

    existing = {
        str(item.get("serial_number") or "").upper(): item
        for item in rows(api("get", "/inventory/items", token))
    }

    added = updated = retired = 0

    for code, name, sku, ecp, dtp, unit, stock, case, hsn, spec in CATALOGUE:
        attributes = {
            "rate": ecp, "rate_per_unit": ecp, "dtp_rate": dtp,
            "unit": unit, "instock": stock, "stock": stock,
            "case_size": case, "hsn_code": hsn,
        }

        if spec:
            attributes["description"] = spec

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
            r = api("put", f"/inventory/items/{current['_id']}", token, json=payload)
            if r.status_code < 400:
                updated += 1
            else:
                print(f"  could not update {sku}: {r.status_code} {r.text[:120]}")

    if replace:
        current_skus = {sku.upper() for _, _, sku, *_ in CATALOGUE}

        for serial, item in existing.items():
            if serial in current_skus:
                continue

            if serial.startswith(RETIRED_PREFIXES):
                r = api("delete", f"/inventory/items/{item['_id']}", token)
                if r.status_code < 400:
                    retired += 1
                else:
                    print(f"  could not retire {serial}: {r.status_code}")

    print(f"\n  {added} added, {updated} updated, {retired} old lines retired")
    print(f"  {len(CATALOGUE)} products in the Synergy catalogue")

    unpriced = [s for _, _, s, e, *_ in CATALOGUE if not e]
    no_dealer = [s for _, _, s, _, d, *_ in CATALOGUE if not d]

    if unpriced:
        print(f"  {len(unpriced)} with no end customer price: {', '.join(unpriced)}")

    print(f"  {len(no_dealer)} with no dealer price")

    return 0


if __name__ == "__main__":
    sys.exit(main())
