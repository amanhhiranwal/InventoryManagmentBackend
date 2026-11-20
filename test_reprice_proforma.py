"""reprice_proforma.py puts a mis-priced proforma right, and only that.

A proforma is broken the way PI-00360 was - its AI line at 0.00 against an
order that sold it at 700, and the total short by it - then:

  * without --apply it shows the change and writes nothing;
  * with --apply the AI line is 700 again, the total is the order's, the
    number is kept, any amount paid stays, and the activity says so;
  * run again, there is nothing to do;
  * an invoice already sent to the customer is refused, as is a number
    that does not exist.

The order and invoice are removed afterwards.

    docker exec -w /app backend_app python test_reprice_proforma.py
"""

import copy
import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from app.database.postgres import SessionLocal
from app.models.proforma_invoice import ProformaInvoice
from app.services import sales_order_service as sos
from seed_sales_team import TEAM_PASSWORD, api, email_for, login, sign_off_order

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def run(*args):
    done = subprocess.run([sys.executable, "reprice_proforma.py", *args], capture_output=True, text=True, cwd="/app")
    return done.returncode, done.stdout + done.stderr


def stored(invoice_id):
    session = SessionLocal()
    row = session.get(ProformaInvoice, invoice_id)
    snapshot = {
        "ai": next((line["rate"] for line in row.items if line.get("sku") == sos.AI_SKU), None),
        "grand_total": float(row.grand_total or 0),
        "balance_due": float(row.balance_due or 0),
        "amount_paid": float(row.amount_paid or 0),
        "pi_number": row.pi_number,
    }
    session.close()
    return snapshot


catalogue = sos._catalogue()
panel = next(sku for sku, e in catalogue.items() if e.get("type") in sos.AI_PRODUCT_TYPES and (e.get("ecp") or 0) > 0)
owner = login(email_for("am_north_1"), TEAM_PASSWORD)
orders, invoices = [], []

try:
    r = api("post", "/orders", owner, json={
        "customer_name": "Reprice Proforma Test", "company_name": "Synergy North Agro",
        "customer_type": "End Customer", "state": "Delhi",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [
            {"product": "Interactive Flat Panel", "model": panel, "sku": panel, "qty": 1,
             "rate": catalogue[panel]["ecp"], "tax_rate": 18, "ai": True},
            {"product": "AI", "model": "AI", "sku": sos.AI_SKU, "qty": 1, "rate": 700, "tax_rate": 18},
        ],
    })
    order = r.json()["data"]
    orders.append(order["id"])
    sign_off_order(order, owner)
    api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})
    order = api("get", f"/orders/{order['id']}", owner).json()["data"]

    r = api("post", "/proforma-invoices", owner, json={"sales_order_id": order["id"], "status": "GENERATED"})
    invoice = r.json()["data"]
    invoices.append(invoice["id"])
    check("an order and its proforma are raised", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    # Broken the way PI-00360 was: AI at 0.00, the total short by 700 + GST,
    # and an advance already typed on it.
    session = SessionLocal()
    row = session.get(ProformaInvoice, invoice["id"])
    items = copy.deepcopy(row.items)
    for line in items:
        if line.get("sku") == sos.AI_SKU:
            line["rate"] = line["price"] = 0.0
    row.items = items
    row.total_amount = float(row.total_amount) - 700
    row.taxable_amount = float(row.taxable_amount) - 700
    row.gst_amount = float(row.gst_amount) - 126
    row.grand_total = float(row.grand_total) - 826
    row.amount_paid = 1000.0
    row.balance_due = row.grand_total - 1000
    session.commit()
    session.close()
    broken = stored(invoice["id"])
    check("the proforma is broken as PI-00360 was", broken["ai"] == 0.0, str(broken))

    print("\nWithout --apply")
    code, out = run(invoice["pi_number"])
    check("it shows the AI line going from 0 to 700", code == 0 and "changes" in out and "700" in out, out[-400:])
    check("and writes nothing", stored(invoice["id"]) == broken, str(stored(invoice["id"])))

    print("\nWith --apply")
    code, out = run(invoice["pi_number"], "--apply")
    fixed = stored(invoice["id"])
    check("it is saved", code == 0 and "saved" in out, out[-300:])
    check("the AI line is 700 again", fixed["ai"] == 700, str(fixed))
    check("the total is the order's", fixed["grand_total"] == float(order["grand_total"]),
          f"{fixed['grand_total']} vs {order['grand_total']}")
    check("the number is kept", fixed["pi_number"] == invoice["pi_number"], fixed["pi_number"])
    check("the amount paid stays, and the balance follows",
          fixed["amount_paid"] == 1000 and fixed["balance_due"] == fixed["grand_total"] - 1000, str(fixed))
    activity = api("get", f"/proforma-invoices/{invoice['id']}/activities", owner)
    check("the activity says so", "Prices Corrected" in activity.text, activity.text[:200])

    print("\nRun again")
    code, out = run(invoice["pi_number"], "--apply")
    check("there is nothing to do", code == 0 and "Nothing to do" in out, out[-200:])

    print("\nWhat it refuses")
    session = SessionLocal()
    session.execute(text("update sales_proforma_invoice set status = 'SENT' where id = :id"), {"id": invoice["id"]})
    session.commit()
    session.close()
    code, out = run(invoice["pi_number"], "--apply")
    check("an invoice already sent is refused", code == 1 and "cancel it and raise a new one" in out, out[-200:])
    code, out = run("PI-99999999", "--apply")
    check("a number that does not exist is refused", code == 1 and "No proforma invoice" in out, out[-200:])
finally:
    session = SessionLocal()
    if invoices:
        session.execute(text("delete from sales_proforma_invoice_activity where proforma_invoice_id = any(:ids)"), {"ids": invoices})
        session.execute(text("delete from notifications where module = 'proforma_invoice' and entity_id = any(:ids)"), {"ids": invoices})
        session.execute(text("delete from sales_proforma_invoice where id = any(:ids)"), {"ids": invoices})
    if orders:
        session.execute(text("delete from sales_approval where document_type = 'SALES_ORDER' and document_id = any(:ids)"), {"ids": orders})
        session.execute(text("delete from notifications where module = 'sales_order' and entity_id = any(:ids)"), {"ids": orders})
        session.execute(text("delete from sales_order_activity where sales_order_id = any(:ids)"), {"ids": orders})
        session.execute(text("delete from sales_order where id = any(:ids)"), {"ids": orders})
    session.commit()
    session.close()
    print("   removed the test order and invoice")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
