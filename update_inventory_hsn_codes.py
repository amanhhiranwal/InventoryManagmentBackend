"""Update HSN codes for inventory items based on model numbers.

Usage:
    python update_inventory_hsn_codes.py          # Dry-run
    python update_inventory_hsn_codes.py --commit # Apply changes
"""

import argparse
import sys
from app.database.mongodb import sync_mongo_db

# Mapping transcribed from production stock sheet annotations
HSN_MAPPINGS = {
    # 110" Displays (HSN 85285900)
    '110" CPX-CVT311D2': "85285900",

    # 65" Displays (HSN 85249990)
    '65" CPX-LangoV100': "85249990",
    '65" CPX-YS 3576': "85249990",
    '65" SPX-3576': "85249990",
    '65" SPX-CVT9679': "85249990",
    '65" SPX-LangoV100': "85249990",

    # 75" Displays (HSN 84714190)
    '75" CPX-CVT9679': "84714190",
    '75" CPX-LangoV100': "84714190",
    '75" CPX-YS 3576': "84714190",
    '75" SPX-3576': "84714190",
    '75" SPX-CVT9679': "84714190",
    '75" SPX-LangoV100': "84714190",

    # 86" Displays (HSN 85249990)
    '86" CPX-LangoV100': "85249990",
    '86" CPX-YS 3576': "85249990",
    '86" SPX-3576': "85249990",
    '86" SPX-CVT9679': "85249990",
    '86" SPX-LangoV100': "85249990",

    # 98" Displays (HSN 85285900)
    '98" CPX-LangoV100': "85285900",

    # OPS PC Modules (HSN 85291029)
    "OPS-i5-12G Asian Non Assembled": "85291029",
    "OPS-i5-12G Euro Non Assembled": "85291029",
    "OPS-i5-12G-8/256 Asian": "85291029",
    "OPS-i5-12G-8/256 Euro": "85291029",
    "OPS-i5-12G-8/512 Asian": "85291029",
    "OPS-i5-12G-8/512 Euro": "85291029",
    "OPS-i7-13G Asian Non Assembled": "85291029",
    "OPS-i7-13G Euro Non Assembled": "85291029",
    "OPS-i7-13G-8/256 Asian": "85291029",
    "OPS-i7-13G-8/256 Euro": "85291029",
    "OPS-i7-13G-8/512 Asian": "85291029",
    "OPS-i7-13G-8/512 Euro": "85291029",

    # Standees (HSN 85437099)
    "Standee 43 inch A type": "85437099",
    "Standee 43 inch I type": "85437099",
    "Standee 49 inch I type": "85437099",
    "Standee 55 inch I type": "85437099",
    "Standee 65 inch I type": "85437099",
}


def run(commit: bool = False):
    col = sync_mongo_db["inventory_items"]
    total_matched = 0
    total_modified = 0

    print(f"{'=' * 60}")
    print(f"Mode: {'COMMIT (Applying changes)' if commit else 'DRY-RUN (No changes applied)'}")
    print(f"{'=' * 60}\n")

    for model, hsn in HSN_MAPPINGS.items():
        count = col.count_documents({"attributes.model_number": model})
        total_matched += count
        if commit and count > 0:
            res = col.update_many(
                {"attributes.model_number": model},
                {"$set": {"attributes.hsn_code": hsn}},
            )
            total_modified += res.modified_count
            print(f"✓ {model} -> HSN {hsn} ({res.modified_count}/{count} updated)")
        else:
            print(f"• {model} -> HSN {hsn} ({count} items matched)")

    print(f"\n{'=' * 60}")
    print(f"Total matched items: {total_matched}")
    if commit:
        print(f"Total modified items: {total_modified}")
    else:
        print("Dry-run complete. Re-run with --commit to apply.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update inventory HSN codes")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit changes to database (default is dry-run)",
    )
    args = parser.parse_args()
    run(commit=args.commit)
