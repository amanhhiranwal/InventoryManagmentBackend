"""Check every stock balance against the ledger it comes from.

The ledger is the record of truth; a balance that differs from what its
ledger rows add up to is a bug or a hand edit. This lists any it finds
and changes nothing. Exits 1 when something is out of step, so it can be
run from cron and alert on failure.

    docker exec -w /app backend_app python reconcile_stock.py
"""

import sys

from app.database.postgres import SessionLocal
from app.services.stock.queries import reconcile


def main() -> int:
    db = SessionLocal()
    try:
        mismatches = reconcile(db)
    finally:
        db.close()

    if not mismatches:
        print("Stock balances agree with the ledger.")
        return 0

    print(f"{len(mismatches)} balance(s) differ from the ledger:")
    for m in mismatches:
        print(
            f"  {m['sku']:24s} {m['stock_status']:12s} warehouse {m['warehouse_id']} bin {m['bin_id']}: "
            f"ledger {m['ledger_total']}, balance {m['balance_on_hand']}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
