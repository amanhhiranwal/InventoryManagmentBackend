"""The proforma invoice bills the prices its sales order agreed.

An AI line sold at 700 on SO-00680 printed at 0.00 on its proforma: the
invoice re-priced every line from today's price list, and the AI rate on
the Warranty Terms master no longer said 700. Now:

  * a line on the order is billed at the order's price, even when the
    master or the price list has moved since;
  * the invoice then totals what the order does;
  * a line that is not on the order is still priced from the list, so a
    price sent by the browser is not believed;
  * a SKU the order holds at two prices is priced from the list;
  * with no order to read, the invoice is priced exactly as before.

The first part checks the pricing in-process, with the AI rate stood in
for. The second does it for real: an order with a panel and AI at 700 is
raised and approved, the AI rate on the master is set to 0, and the
proforma is generated through the API and downloaded. The AI rate is put
back to what it was, and the order and invoice removed, however it ends.

    docker exec -w /app backend_app python test_pi_agreed_prices.py
"""

import copy
import sys
from types import SimpleNamespace

from app.services import sales_order_service as sos
from app.services import warranty_term_service
from app.services.proforma_invoice_service import _totals

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


catalogue = sos._catalogue()
panel_sku = next((sku for sku, entry in catalogue.items() if (entry.get("ecp") or 0) > 0), None)
if panel_sku is None:
    print("No product with a price in the catalogue to test with.")
    sys.exit(1)
list_price = float(catalogue[panel_sku]["ecp"])


def line(sku, rate, qty=1, **extra):
    return {"sku": sku, "product": sku, "rate": rate, "price": rate, "qty": qty, "quantity_case": qty,
            "discount": 0, "tax_rate": 18, **extra}


def invoice_for(items, order):
    return SimpleNamespace(
        items=copy.deepcopy(items), customer_type="End Customer",
        discount_mode=None, discount_input=None, orc_mode=None, orc_input=None,
        freight_charges=0, installation_lumpsum=0, gst_percent=18, amount_paid=0,
        sales_order=order,
    )


def rate_of(invoice, sku):
    return next(row["rate"] for row in invoice.items if row["sku"] == sku)


real_ai_rate = warranty_term_service.ai_rate
try:
    # The AI rate on the master has moved to nothing since the order was priced.
    warranty_term_service.ai_rate = lambda: 0.0

    order_items = [line(panel_sku, list_price, ai=True), line(sos.AI_SKU, 700)]
    order = SimpleNamespace(items=order_items)

    print("\nAn invoice raised from the order")
    invoice = invoice_for(order_items, order)
    totals = _totals(invoice)
    check("the AI line is billed at the order's 700", rate_of(invoice, sos.AI_SKU) == 700, str(rate_of(invoice, sos.AI_SKU)))
    check("the panel at the order's price", rate_of(invoice, panel_sku) == list_price, str(rate_of(invoice, panel_sku)))
    check("the subtotal is the order's", totals["total_amount"] == round(list_price + 700, 2),
          f"{totals['total_amount']} vs {list_price + 700}")
    check("and the GST on it", round(totals["gst_amount"], 2) == round((list_price + 700) * 0.18, 2), str(totals.get("gst_amount")))

    print("\nA price the order does not hold")
    sent = [line(panel_sku, list_price, ai=True), line(sos.AI_SKU, 700)]
    invoice = invoice_for(sent, SimpleNamespace(items=[line(panel_sku, list_price)]))
    _totals(invoice)
    check("a line not on the order is priced from the list, not as sent",
          rate_of(invoice, sos.AI_SKU) == 0.0, str([(r["sku"], r["rate"]) for r in invoice.items]))

    print("\nA SKU the order holds at two prices")
    two = SimpleNamespace(items=[line(panel_sku, list_price), line(panel_sku, list_price + 5000)])
    invoice = invoice_for([line(panel_sku, list_price + 5000)], two)
    _totals(invoice)
    check("it is priced from the list", rate_of(invoice, panel_sku) == list_price, str(rate_of(invoice, panel_sku)))

    print("\nA line sold above its list price")
    from fastapi import HTTPException

    above = SimpleNamespace(items=[line(panel_sku, list_price + 5000, marked_up=True)])
    invoice = invoice_for([line(panel_sku, list_price + 5000, marked_up=True, discount=5)], above)
    try:
        _totals(invoice)
        check("still takes no discount on the invoice", False, "no error raised")
    except HTTPException as error:
        check("still takes no discount on the invoice", error.status_code == 400 and "no discount" in str(error.detail),
              str(error.detail))
    invoice = invoice_for([line(panel_sku, list_price + 5000, marked_up=True)], above)
    _totals(invoice)
    check("and without one is billed at its selling price", rate_of(invoice, panel_sku) == list_price + 5000,
          str(rate_of(invoice, panel_sku)))

    print("\nWith no order to read")
    invoice = invoice_for(order_items, None)
    _totals(invoice)
    check("the invoice is priced from the list, as before", rate_of(invoice, sos.AI_SKU) == 0.0, str(rate_of(invoice, sos.AI_SKU)))
    check("and the order's own lines are untouched", order_items[1]["rate"] == 700, str(order_items[1]))
finally:
    warranty_term_service.ai_rate = real_ai_rate


# ---------------------------------------------------------------- for real
print("\nFor real: the AI rate changed between the order and its proforma")

import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.database.postgres import SessionLocal  # noqa: E402
from app.models.app_setting import AppSetting  # noqa: E402
from seed_sales_team import TEAM_PASSWORD, api, email_for, login, sign_off_order  # noqa: E402

TAG = uuid.uuid4().hex[:6].upper()
panel = next((sku for sku, entry in catalogue.items() if entry.get("type") in sos.AI_PRODUCT_TYPES
              and (entry.get("ecp") or 0) > 0), None)
owner = login(email_for("am_north_1"), TEAM_PASSWORD)
orders, invoices = [], []

session = SessionLocal()
setting = session.query(AppSetting).filter(AppSetting.key == warranty_term_service.AI_RATE_KEY).first()
rate_before = None if setting is None else setting.value
session.close()

try:
    if panel is None:
        check("an Interactive Flat Panel with a price is in the catalogue", False)
        raise SystemExit

    # The order is priced while the AI rate is 700.
    session = SessionLocal()
    warranty_term_service.save_ai_rate(session, 700)
    session.close()

    r = api("post", "/orders", owner, json={
        "customer_name": f"PI Price Test {TAG}",
        "company_name": "Synergy North Agro",
        "customer_type": "End Customer",
        "state": "Delhi",
        "order_date": datetime.now(timezone.utc).isoformat(),
        "items": [
            {"product": "Interactive Flat Panel", "model": panel, "sku": panel, "qty": 1,
             "rate": catalogue[panel]["ecp"], "tax_rate": 18, "ai": True},
            {"product": "AI", "model": "AI", "sku": sos.AI_SKU, "qty": 1, "rate": 700, "tax_rate": 18},
        ],
    })
    check("an order with a panel and AI is raised", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    order = r.json()["data"]
    orders.append(order["id"])
    ai_on_order = next(row for row in order["items"] if row["sku"] == sos.AI_SKU)
    check("the order holds AI at 700", ai_on_order["rate"] == 700, str(ai_on_order))

    sign_off_order(order, owner)
    r = api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})
    check("it is approved and confirmed", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    order = api("get", f"/orders/{order['id']}", owner).json()["data"]

    # Then the AI rate on the master changes.
    session = SessionLocal()
    warranty_term_service.save_ai_rate(session, 0)
    session.close()
    check("the AI rate on the master is now 0", warranty_term_service.ai_rate() == 0.0, str(warranty_term_service.ai_rate()))

    r = api("post", "/proforma-invoices", owner, json={"sales_order_id": order["id"], "status": "GENERATED"})
    check("the proforma is generated", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    invoice = r.json()["data"]
    invoices.append(invoice["id"])

    ai_on_invoice = next((row for row in invoice["items"] if row["sku"] == sos.AI_SKU), {})
    check("its AI line is 700, as the order agreed", ai_on_invoice.get("rate") == 700, str(ai_on_invoice))
    check("and it totals what the order does",
          float(invoice["grand_total"]) == float(order["grand_total"]),
          f"{invoice['grand_total']} vs {order['grand_total']}")

    pdf = api("get", f"/proforma-invoices/{invoice['id']}/pdf", owner)
    check("the PDF downloads", pdf.status_code == 200 and pdf.content[:4] == b"%PDF", str(pdf.status_code))
    # The page text: ReportLab writes each stream ASCII85-encoded, then compressed.
    import base64  # noqa: E402
    import re  # noqa: E402
    import zlib  # noqa: E402

    printed = b""
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", pdf.content, re.S):
        try:
            printed += zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
        except (ValueError, zlib.error):
            pass
    amounts = re.findall(rb"\(([\d,]+\.\d\d)\)", printed)
    check("and the PDF prints AI at 700.00", b"700.00" in amounts, str(amounts[:12]))
finally:
    session = SessionLocal()
    row = session.query(AppSetting).filter(AppSetting.key == warranty_term_service.AI_RATE_KEY).first()
    if rate_before is None and row is not None:
        session.delete(row)
    elif row is not None:
        row.value = rate_before
    if invoices:
        session.execute(text("delete from sales_proforma_invoice_activity where proforma_invoice_id = any(:ids)"), {"ids": invoices})
        session.execute(text("delete from sales_proforma_invoice where id = any(:ids)"), {"ids": invoices})
    if orders:
        session.execute(text("delete from sales_approval where document_type = 'SALES_ORDER' and document_id = any(:ids)"), {"ids": orders})
        session.execute(text("delete from notifications where module = 'sales_order' and entity_id = any(:ids)"), {"ids": orders})
        session.execute(text("delete from sales_order_activity where sales_order_id = any(:ids)"), {"ids": orders})
        session.execute(text("delete from sales_order where id = any(:ids)"), {"ids": orders})
    session.commit()
    session.close()
    print(f"   AI rate put back to {warranty_term_service.ai_rate():g}; removed the test order and invoice")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
