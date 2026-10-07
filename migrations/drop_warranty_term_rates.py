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

Anything still held in them is printed before it goes, so a rate that was
set and never moved onto a product is not lost silently - it is on screen
to be re-entered against the products it applied to.

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

    carried = [
        r[0]
        for r in db.execute(
            text(f"SELECT name FROM {TABLE} WHERE rate IS NOT NULL AND rate > 0")
        )
    ]

    if carried:
        print(
            "\n  Note: "
            + ", ".join(carried)
            + " had a rate set here. Check each product that offers the term"
            " carries its own figure before dropping these."
        )

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
