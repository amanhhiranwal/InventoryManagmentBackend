"""Drop the warranty rate columns the term no longer owns.

What a warranty term costs used to sit beside its name, which made "5
Years" the same price on every product in the catalogue. Five years on a
panel and five years on a camera are different undertakings, so the rate
moved onto each product - ``attributes.warranty_rates`` on the inventory
record - and the term kept only the length.

``Base.metadata.create_all`` adds columns but never removes them, so
``rate`` and ``rate_mode`` are still on the table, still NOT NULL, and
still carrying whatever was last set. Nothing reads them: the model does
not map them and the pricing path goes to the catalogue. They are inert
rather than wrong, but a column nobody reads is a column somebody will
eventually believe.

Anything still held in them is carried onto the catalogue first. Under the
old model one rate applied to every product, so copying it onto each one
reproduces exactly what was being charged; a product that already has its
own figure for a term keeps it, because that is the newer answer. Without
that step the drop is lossy, and every product quietly starts charging
nothing for extended cover.

    python migrations/drop_warranty_term_rates.py --dry-run
    python migrations/drop_warranty_term_rates.py

Safe to run again: it checks what is there before touching anything.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database.postgres import SessionLocal  # noqa: E402

TABLE = "sales_warranty_term"
COLUMNS = ["rate", "rate_mode"]


def present(db) -> list[str]:
    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"
        ),
        {"t": TABLE},
    )

    held = {r[0] for r in rows}

    return [c for c in COLUMNS if c in held]


def carry_onto_products(db, dry_run: bool) -> int:
    """Put the old per-term rate onto every product, before it is lost.

    Under the old model one rate applied to the whole catalogue, so
    copying it onto each product reproduces exactly what was being
    charged. A product that already carries its own rate for a term is
    left alone: somebody has set that deliberately since, and it is the
    newer answer.

    Without this the drop is lossy - the rate disappears and every
    product silently starts charging nothing for extended cover.
    """

    rates = {
        name: {"mode": (mode or "PERCENT").upper(), "rate": float(rate or 0)}
        for name, rate, mode in db.execute(
            text(f"SELECT name, rate, rate_mode FROM {TABLE} WHERE rate > 0")
        )
    }

    if not rates:
        print("\n  no rates to carry across")
        return 0

    try:
        from app.database.mongodb import sync_mongo_db
    except Exception as error:  # noqa: BLE001
        print(f"\n  cannot reach the catalogue to carry them: {str(error)[:70]}")
        return 0

    touched = 0

    for row in sync_mongo_db["inventory_items"].find(
        {}, {"serial_number": 1, "attributes.warranty_rates": 1}
    ):
        held = (row.get("attributes") or {}).get("warranty_rates") or {}

        # Only the terms this product has no answer for.
        missing = {k: v for k, v in rates.items() if k not in held}

        if not missing:
            continue

        if not dry_run:
            sync_mongo_db["inventory_items"].update_one(
                {"_id": row["_id"]},
                {"$set": {"attributes.warranty_rates": {**held, **missing}}},
            )

        touched += 1

    verb = "would carry" if dry_run else "carried"
    terms = ", ".join(sorted(rates))

    print(f"\n  {verb} {terms} onto {touched} products that had no rate set")

    return touched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would go, drop nothing"
    )
    args = parser.parse_args()

    db = SessionLocal()

    found = present(db)

    if not found:
        print(f"{TABLE} already has neither rate nor rate_mode. Nothing to do.")
        return 0

    print(f"{TABLE} still holds: {', '.join(found)}\n")

    # Show what is in them first. A rate somebody set and never carried onto
    # a product is worth seeing before it is gone.
    selected = ", ".join(["name"] + found)
    print("What those columns currently hold:\n")

    for row in db.execute(text(f"SELECT {selected} FROM {TABLE} ORDER BY years")):
        print("   ", dict(zip(["name"] + found, row)))

    # Carried onto the products before anything is dropped, so the rate
    # survives the move rather than needing re-entering by hand.
    carry_onto_products(db, args.dry_run)

    if args.dry_run:
        print(f"\nWould drop: {', '.join(found)} from {TABLE}")
        return 0

    for column in found:
        db.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {column}"))
        print(f"\n  dropped {TABLE}.{column}")

    db.commit()

    left = present(db)
    print(
        f"\n{TABLE} now holds neither rate nor rate_mode."
        if not left
        else f"\nStill present: {', '.join(left)}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
