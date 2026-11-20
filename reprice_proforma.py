"""Re-total one proforma invoice at the prices its sales order agreed.

A proforma used to re-price every line from the price list when it was
generated. PI-00360 billed its AI line at 0.00 against an order that sold
it at 700, because the AI rate on the Warranty Terms master no longer
said 700 by then. New invoices bill the order's prices; this puts one
already generated right, keeping its number.

It shows what would change and writes nothing unless told to:

    docker exec -w /app backend_app python reprice_proforma.py PI-00360
    docker exec -w /app backend_app python reprice_proforma.py PI-00360 --apply

Only a draft or generated invoice is changed. One already sent to the
customer is refused: the customer holds that copy, so it is cancelled and
a new one raised instead. Any amount paid is kept, and the balance due
worked out again. The change is written to the invoice's activity.
"""

import copy
import sys

from app.core.workflow_status import ProformaInvoiceStatus
from app.database.postgres import SessionLocal
from app.models.proforma_invoice import ProformaInvoice
from app.services.proforma_invoice_service import ProformaInvoiceService, _totals
from app.services.quotation_service import format_inr

FIXABLE = (ProformaInvoiceStatus.DRAFT, ProformaInvoiceStatus.GENERATED)


def money(value) -> str:
    return format_inr(float(value or 0))


def main(argv: list[str]) -> int:
    numbers = [arg for arg in argv if not arg.startswith("--")]
    apply = "--apply" in argv

    if len(numbers) != 1:
        print(__doc__)
        return 2

    number = numbers[0].strip().upper()
    db = SessionLocal()

    try:
        invoice = db.query(ProformaInvoice).filter(ProformaInvoice.pi_number == number).first()

        if invoice is None:
            print(f"No proforma invoice {number}.")
            return 1

        if invoice.status not in FIXABLE:
            print(
                f"{number} is {invoice.status.title()}, so it is not changed here. "
                + ("The customer has this copy: cancel it and raise a new one."
                   if invoice.status == ProformaInvoiceStatus.SENT else "")
            )
            return 1

        order = invoice.sales_order
        if order is None:
            print(f"{number} has no sales order to take the prices from.")
            return 1

        # A copy, assigned back below: changed in place, the stored list is
        # compared with itself and nothing is written (see repair_lines.py).
        before_items = copy.deepcopy(invoice.items or [])
        items = copy.deepcopy(invoice.items or [])
        totals = _totals(invoice, order, items=items)

        print(f"{number}, raised on {order.order_number} for {invoice.customer_name}, {invoice.status.title()}\n")

        changed_lines = 0
        for old, new in zip(before_items, items):
            name = new.get("product") or new.get("model") or new.get("sku")
            was, now = float(old.get("rate") or 0), float(new.get("rate") or 0)
            mark = "  changes" if abs(was - now) > 0.005 else ""
            changed_lines += bool(mark)
            print(f"  {name:<32} {money(was):>14} -> {money(now):>14}{mark}")

        print()
        changed_totals = []
        for key, label in (("total_amount", "Subtotal"), ("taxable_amount", "Taxable"),
                           ("gst_amount", "GST"), ("grand_total", "Total"), ("balance_due", "Balance due")):
            was, now = getattr(invoice, key), totals.get(key)
            if abs(float(was or 0) - float(now or 0)) > 0.005:
                changed_totals.append((label, was, now))
            print(f"  {label:<32} {money(was):>14} -> {money(now):>14}")

        if not changed_lines and not changed_totals:
            print(f"\n{number} already bills its order's prices. Nothing to do.")
            return 0

        if not apply:
            print("\nNothing written. Run again with --apply to save this.")
            return 0

        invoice.items = items
        for key, value in totals.items():
            setattr(invoice, key, value)

        ProformaInvoiceService.record_activity(
            db,
            invoice,
            action="Prices Corrected",
            description=(
                f"Lines re-totalled at the prices agreed on {order.order_number}. "
                + "; ".join(f"{label} {money(was)} to {money(now)}" for label, was, now in changed_totals)
                + "."
            ),
            commit=False,
        )
        db.add(invoice)
        db.commit()

        print(f"\n{number} saved. Download it again for the corrected PDF.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
