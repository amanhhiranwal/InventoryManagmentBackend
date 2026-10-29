"""An approved order from the business coordinator to delivery.

    Approved -> the coordinator records the advance and accepts it
             -> the coordinator processes it (nobody else can)
             -> accounts verify it, and the balance is kept on the order
             -> production name a serial for every unit; each is checked
                against the shelf and issued when the order is ready
             -> logistics upload the invoice, insurance and docket copies,
                dispatch it, mark it out for delivery, then delivered

Before that, a rejected order resent at list price: its rejection shows
while it is a draft and not once it is approved. After it, the
coordinator's view - every approved order on the Sales Order pages, to
read and track, never to change.

It checks the refusals too: the wrong role, an advance of nothing, the
wrong number of serials, a serial twice, one not on the shelf, dispatch
without the documents - and that holding an order after its units were
issued puts them back on the shelf.

Run seed_sales_team.py and seed_fulfilment_roles.py first. Everything it
creates is removed again, catalogue units included.

    docker exec -w /app backend_app python test_order_journey.py
"""

import sys
import uuid
from datetime import datetime, timezone

from seed_sales_team import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    TEAM_PASSWORD,
    api,
    email_for,
    login,
    rows,
    sign_off_order,
)

TAG = uuid.uuid4().hex[:6].upper()
MODEL = f"JRN-{TAG}"
SERIALS = [f"JRN-{TAG}-{n}" for n in (1, 2, 3)]

passed, failed = [], []
section = ""


def check(name, condition, detail=""):
    (passed if condition else failed).append(f"{section}: {name}")
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + name
        + (f"   [{detail}]" if detail and not condition else "")
    )


def banner(title):
    global section
    section = title
    print(f"\n{title}")


token = {}
created_orders: list[int] = []
created_invoices: list[int] = []
created_keys: list[str] = []

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    token["owner"] = login(email_for("am_north_1"), TEAM_PASSWORD)
    token["ceo"] = login(email_for("ceo"), TEAM_PASSWORD)

    for key in ("coordinator", "accounts", "production", "logistics"):
        token[key] = login(f"{key}@mailinator.com", TEAM_PASSWORD)

    owner = token["owner"]
    now = datetime.now(timezone.utc)

    print("Signed in as the super admin, a salesperson and the four desks.")

    # A product of our own, three units with their own serials, so the
    # shelf this test counts down is one nobody else is using.
    for serial in SERIALS:
        made = api("post", "/inventory/items", admin, json={
            "name": f"Journey Panel {TAG}",
            "serial_number": serial,
            "product_type_code": "DISPLAY",
            "category": "Interactive Panels",
            "attributes": {
                "rate": 1000,
                "unit": "Nos",
                "instock": 1,
                "case_size": 1,
                "model_number": MODEL,
                "specification": "Test unit",
            },
        })

        if made.status_code >= 400:
            raise RuntimeError(f"could not stock {serial}: {made.text[:160]}")

    def raise_order(label, qty):
        created = api("post", "/orders", owner, json={
            "customer_name": f"{label} {TAG}",
            "company_name": "Journey Test School",
            "state": "Uttar Pradesh",
            "order_date": now.isoformat(),
            "shipping_address": {
                "street": "1 Test Road", "city": "Noida",
                "state": "Uttar Pradesh", "pin": "201301",
            },
            "items": [{
                "product": f"Journey Panel {TAG}",
                "sku": MODEL,
                "qty": qty,
                "rate": 1000,
                "tax_rate": 18,
            }],
        })

        order = created.json()["data"]
        created_orders.append(order["id"])

        sign_off_order(order, owner)
        api("put", f"/orders/{order['id']}/status", owner, json={"status": "CONFIRMED"})

        raised = api("post", "/proforma-invoices", owner, json={
            "sales_order_id": order["id"],
            "status": "DRAFT",
            "advance_percent": 30,
        })
        created_invoices.append(raised.json()["data"]["id"])

        return api("get", f"/orders/{order['id']}", admin).json()["data"]

    def status_of(order_id):
        return api("get", f"/orders/{order_id}", admin).json()["data"]["status"]

    def decide(who, order_id, **body):
        return api("put", f"/fulfilment/orders/{order_id}/decide", token[who], json={
            "approve": True, **body,
        })

    def on_desk(desk, who, order_id):
        queue = api("get", f"/fulfilment/{desk}", token[who]).json()["data"]
        return next((o for o in queue["orders"] if o["id"] == order_id), None), queue

    # ======================================== 0. a rejection, superseded
    banner("0. A rejection shows while the order is a draft, not once it is approved")

    rejected = api("post", "/orders", owner, json={
        "customer_name": f"Rejected {TAG}",
        "company_name": "Journey Test School",
        "state": "Uttar Pradesh",
        "order_date": now.isoformat(),
        "items": [{"product": f"Journey Panel {TAG}", "sku": MODEL, "qty": 1, "rate": 1000, "tax_rate": 18}],
    }).json()["data"]
    created_orders.append(rejected["id"])

    def send_up(order, discount):
        return api("post", "/approvals", owner, json={
            "document_type": "SALES_ORDER",
            "document_id": order["id"],
            "document_number": order.get("order_number"),
            "price_type": "ECP",
            "discount_percent": discount,
            "document_value": order.get("grand_total") or 0,
        })

    raised = send_up(rejected, 12).json()["data"]
    api("put", f"/approvals/{raised['id']}/decide", admin, json={"approve": False, "remarks": "Too much off."})

    standing = api("get", f"/approvals/document/SALES_ORDER/{rejected['id']}", owner).json()["data"]
    check("a rejected order goes back to draft", status_of(rejected["id"]) == "DRAFT", status_of(rejected["id"]))
    check("and the draft shows the rejection", bool(standing) and standing["status"] == "REJECTED", str(standing and standing["status"]))

    resent = send_up(rejected, 0)
    check("resent at list price, it needs no signature", (resent.json() or {}).get("data") is None, resent.text[:120])
    api("put", f"/orders/{rejected['id']}/status", owner, json={"status": "CONFIRMED"})
    check("so it is approved", status_of(rejected["id"]) == "CONFIRMED", status_of(rejected["id"]))

    standing = api("get", f"/approvals/document/SALES_ORDER/{rejected['id']}", owner).json()["data"]
    check("and the old rejection no longer shows on it", standing is None, str(standing and standing["status"]))

    # ============================================ 1. the coordinator's desk
    banner("1. An approved order goes to the business coordinator")

    order = raise_order("Journey", qty=2)
    oid = order["id"]

    check("the order is approved", status_of(oid) == "CONFIRMED", status_of(oid))

    mine, queue = on_desk("coordination", "coordinator", oid)
    check("it lands on the coordinator's desk", mine is not None)
    check("not on the accounts desk", on_desk("accounts", "accounts", oid)[0] is None)
    check(
        "the desk shows the payment terms",
        bool(mine and mine.get("payment_terms") is not None or mine and mine.get("advance_percent")),
        str(mine and mine.get("payment_terms")),
    )
    check(
        "the desk has its own numbers",
        {"awaiting_check", "ready_to_process", "short_of_terms"} <= {k["key"] for k in queue["kpis"]},
        str([k["key"] for k in queue["kpis"]]),
    )

    for who in ("accounts", "production", "logistics", "owner"):
        check(
            f"{who} cannot open the coordinator's desk",
            api("get", "/fulfilment/coordination", token[who]).status_code == 403,
        )

    # ===================================== 1b. the Sales Order pages
    banner("1b. The coordinator reads every approved order on the Sales Order pages")

    listed = api("get", "/orders", token["coordinator"], params={"page": 1, "size": 500}).json()["data"]
    listed = listed.get("items", listed) if isinstance(listed, dict) else listed
    statuses = {o["status"] for o in listed}
    check("the list holds this order, raised by somebody else", oid in {o["id"] for o in listed})
    check("and no draft or order waiting on approval", not statuses & {"DRAFT", "PENDING_APPROVAL"}, str(statuses))

    draft = api("post", "/orders", owner, json={
        "customer_name": f"Draft {TAG}",
        "order_date": now.isoformat(),
        "items": [{"product": "Panel", "qty": 1, "rate": 1000, "tax_rate": 18}],
    }).json()["data"]
    created_orders.append(draft["id"])
    check("a draft cannot be opened", api("get", f"/orders/{draft['id']}", token["coordinator"]).status_code == 403)

    opened = api("get", f"/orders/{oid}", token["coordinator"])
    check("the approved order opens", opened.status_code == 200, f"{opened.status_code}")
    check("with its sales order PDF", bool(opened.json()["data"].get("can_download_pdf")))
    check("and its proforma invoice", len(rows(api("get", "/proforma-invoices", token["coordinator"], params={"sales_order_id": oid}))) == 1)
    check("and its history", api("get", f"/orders/{oid}/activities", token["coordinator"]).status_code == 200)

    edited = api("put", f"/orders/{oid}", token["coordinator"], json={"remarks": "Changed by the coordinator."})
    check("but the coordinator cannot change it", edited.status_code == 403, f"{edited.status_code} {edited.text[:100]}")

    # ======================================== 2. only they can process it
    banner("2. Process Order is the coordinator's, and only after the advance")

    r = api("post", f"/orders/{oid}/process", token["ceo"])
    check("the CEO cannot process it", r.status_code == 403, f"{r.status_code} {r.text[:100]}")

    r = api("post", f"/orders/{oid}/process", owner)
    check("the salesperson cannot process it", r.status_code == 403, f"{r.status_code} {r.text[:100]}")

    r = api("post", f"/orders/{oid}/process", token["accounts"])
    check("accounts cannot process it", r.status_code == 403, f"{r.status_code}")

    r = api("post", f"/orders/{oid}/process", token["coordinator"])
    check("the coordinator cannot process it before the advance", r.status_code == 409, f"{r.status_code} {r.text[:100]}")

    # ================================================ 3. the advance
    banner("3. The coordinator records the advance and accepts it")

    r = api("put", f"/orders/{oid}/status", token["coordinator"], json={"status": "ADVANCE_ACCEPTED"})
    check("the coordinator cannot skip the advance through the status endpoint", r.status_code == 409, f"{r.status_code}")

    r = decide("coordinator", oid, advance_amount=0)
    check("an advance of nothing is refused", r.status_code == 400, f"{r.status_code}")

    r = decide("accounts", oid, advance_amount=100)
    check("accounts cannot accept it for them", r.status_code == 403, f"{r.status_code}")

    advance = round(float(order["grand_total"]) * 0.3, 2)
    r = decide("coordinator", oid, advance_amount=advance, remarks="NEFT received.")
    check("the coordinator accepts it", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("the order moves to advance accepted", status_of(oid) == "ADVANCE_ACCEPTED", status_of(oid))

    invoice = api("get", f"/proforma-invoices/{created_invoices[0]}", admin).json()["data"]
    check(
        "the advance is on the proforma invoice",
        abs(float(invoice["amount_paid"]) - advance) < 1,
        str(invoice["amount_paid"]),
    )

    # ================================================ 4. processing it
    banner("4. The coordinator processes it to accounts")

    r = api("post", f"/orders/{oid}/process", token["coordinator"])
    check("the coordinator processes it", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("the order is with accounts", status_of(oid) == "ORDER_PROCESSED", status_of(oid))

    mine, _ = on_desk("accounts", "accounts", oid)
    check("it lands on the accounts desk", mine is not None)
    check(
        "accounts see the advance received, not nothing",
        bool(mine and abs(mine["advance_received"] - advance) < 1),
        str(mine and mine["advance_received"]),
    )

    # ============================================= 5. accounts verify
    banner("5. Accounts verify, and the balance is kept")

    r = decide("accounts", oid, remarks="In the bank.")
    check("accounts verify the payment", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("the order is payment verified", status_of(oid) == "PAYMENT_VERIFIED", status_of(oid))

    now_order = api("get", f"/orders/{oid}", admin).json()["data"]
    check(
        "the balance after the advance is stored on the order",
        abs(float(now_order.get("outstanding_balance") or 0) - (float(order["grand_total"]) - advance)) < 1,
        str(now_order.get("outstanding_balance")),
    )

    # ====================================== 6. production name the units
    banner("6. Production name a serial for every unit")

    r = decide("production", oid, remarks="Taken in.")
    check("production take it into procurement", r.status_code == 200 and status_of(oid) == "PROCUREMENT", status_of(oid))

    found = api("get", "/fulfilment/serials/check", token["production"], params={
        "serial": SERIALS[0], "order_id": oid, "line": 0,
    }).json()["data"]
    check("a serial on the shelf is found and in stock", found["found"] and found["in_stock"], str(found))
    check("and recognised as this line's product", found.get("matches_line") is True, str(found))

    missing = api("get", "/fulfilment/serials/check", token["production"], params={
        "serial": f"NOPE-{TAG}",
    }).json()["data"]
    check("a serial that does not exist is reported", missing["found"] is False, str(missing))

    check(
        "logistics cannot use the serial check",
        api("get", "/fulfilment/serials/check", token["logistics"], params={"serial": SERIALS[0]}).status_code == 403,
    )

    r = api("put", f"/orders/{oid}/status", token["production"], json={"status": "READY"})
    check("production cannot skip the serials through the status endpoint", r.status_code == 409, f"{r.status_code} {r.text[:120]}")

    r = decide("production", oid, serials=[{"line": 0, "serial": SERIALS[0]}])
    check("one serial for two units is refused", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

    r = decide("production", oid, serials=[{"line": 0, "serial": SERIALS[0]}, {"line": 0, "serial": SERIALS[0]}])
    check("the same serial twice is refused", r.status_code == 400, f"{r.status_code}")

    r = decide("production", oid, serials=[{"line": 0, "serial": SERIALS[0]}, {"line": 0, "serial": f"NOPE-{TAG}"}])
    check("a serial not on the shelf is refused", r.status_code == 400 and "not in the catalogue" in r.text, r.text[:160])
    check("and nothing moved", status_of(oid) == "PROCUREMENT", status_of(oid))

    r = decide("production", oid, serials=[{"line": 0, "serial": SERIALS[0]}, {"line": 0, "serial": SERIALS[1]}])
    check("both serials mark it ready", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("the order is ready to dispatch", status_of(oid) == "READY", status_of(oid))

    gone = api("get", "/fulfilment/serials/check", token["production"], params={"serial": SERIALS[0]}).json()["data"]
    check("an issued serial is no longer on the shelf", gone["found"] is False and "issued" in (gone.get("message") or ""), str(gone))

    catalogue = rows(api("get", "/inventory/items", admin, params={"search": MODEL}))
    left = sorted(s for item in catalogue for s in (item.get("serial_numbers") or []))
    check("the catalogue now holds only the unit not issued", left == [SERIALS[2]], str(left))

    detail = api("get", f"/fulfilment/orders/{oid}", token["logistics"]).json()["data"]
    issued = [m for m in detail["stock_movements"] if m["direction"] == "OUT"]
    check("an outgoing record is kept for each unit", sorted(m["serial_number"] for m in issued) == SERIALS[:2], str(issued))
    check("each says who issued it and when", all(m.get("actor") and m.get("created_at") for m in issued), str(issued[:1]))

    from app.database.mongodb import sync_mongo_db

    places = [row.get("place") for row in sync_mongo_db["inventory_movements"].find({"order_id": oid, "direction": "OUT"})]
    check("and where it is going", all(p and "Noida" in p for p in places), str(places))

    # ============================================ 7. logistics send it
    banner("7. Logistics upload the documents and send it out")

    mine, queue = on_desk("logistics", "logistics", oid)
    check("it lands on the logistics desk", mine is not None)
    check(
        "the desk counts it as missing documents",
        next(k["value"] for k in queue["kpis"] if k["key"] == "documents_pending") >= 1,
    )

    r = api("put", f"/orders/{oid}/status", token["logistics"], json={"status": "DISPATCHED"})
    check("logistics cannot skip the documents through the status endpoint", r.status_code == 409, f"{r.status_code}")

    r = decide("logistics", oid)
    check("it cannot be dispatched without the documents", r.status_code == 400 and "Invoice Copy" in r.text, r.text[:160])

    uploaded = api(
        "post", "/attachments", token["logistics"],
        files={"file": (f"docket-{TAG}.pdf", b"%PDF-1.4\n%test\n", "application/pdf")},
    ).json()["data"]
    created_keys.append(uploaded["key"])

    r = api("put", f"/fulfilment/orders/{oid}/documents", token["production"], json={"kind": "invoice_copy", **uploaded})
    check("production cannot upload dispatch documents", r.status_code == 403, f"{r.status_code}")

    for kind in ("invoice_copy", "insurance_copy"):
        r = api("put", f"/fulfilment/orders/{oid}/documents", token["logistics"], json={"kind": kind, **uploaded})
        check(f"logistics upload the {kind.replace('_', ' ')}", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    r = decide("logistics", oid)
    check("two of three is still refused", r.status_code == 400 and "Docket Slip" in r.text, r.text[:160])

    r = api("put", f"/fulfilment/orders/{oid}/documents", token["logistics"], json={"kind": "docket_slip", **uploaded})
    check("logistics upload the docket slip", r.status_code == 200, f"{r.status_code}")

    for stage in ("DISPATCHED", "OUT_FOR_DELIVERY", "DELIVERED"):
        r = decide("logistics", oid)
        check(f"logistics mark it {stage.lower().replace('_', ' ')}", r.status_code == 200 and status_of(oid) == stage, f"{r.status_code} {status_of(oid)} {r.text[:100]}")

    # ============================================= 8. the activity log
    banner("8. The sales order's activity log tells the whole story")

    actions = [e.get("action") for e in rows(api("get", f"/orders/{oid}/activities", admin))]

    check(
        "the coordinator still opens it once delivered",
        api("get", f"/orders/{oid}", token["coordinator"]).status_code == 200,
    )

    for expected in (
        "Advance Accepted", "Order Processed", "Payment Verified",
        "Ready To Dispatch", "Invoice Copy Uploaded", "Docket Slip Uploaded",
        "Order Dispatched", "Out For Delivery", "Order Delivered",
    ):
        check(f"it records {expected}", expected in actions, str(actions[:12]))

    # ======================================== 9. a hold puts units back
    banner("9. Holding an order after its units were issued puts them back")

    held = raise_order("Held", qty=1)
    hid = held["id"]

    decide("coordinator", hid, advance_amount=round(float(held["grand_total"]) * 0.3, 2))
    api("post", f"/orders/{hid}/process", token["coordinator"])
    decide("accounts", hid)
    decide("production", hid)
    r = decide("production", hid, serials=[{"line": 0, "serial": SERIALS[2]}])
    check("the last unit is issued to it", r.status_code == 200 and status_of(hid) == "READY", f"{r.status_code} {r.text[:120]}")

    r = api("put", f"/fulfilment/orders/{hid}/decide", token["logistics"], json={
        "approve": False, "remarks": "Customer asked to wait.",
    })
    check("logistics hold it", r.status_code == 200 and status_of(hid) == "ON_HOLD", status_of(hid))

    back = api("get", "/fulfilment/serials/check", token["production"], params={"serial": SERIALS[2]}).json()["data"]
    check("the unit is back on the shelf", back["found"] and back["in_stock"], str(back))

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"{section}: the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    banner("10. Clearing what the test created")

    try:
        from sqlalchemy import text

        from app.database.mongodb import sync_mongo_db
        from app.database.postgres import SessionLocal

        from app.services.inventory_service import InventoryService

        removed_units = InventoryService.items_col.delete_many(
            {"serial_number": {"$in": SERIALS}}
        ).deleted_count

        if created_orders:
            sync_mongo_db["inventory_movements"].delete_many(
                {"order_id": {"$in": created_orders}}
            )

        session = SessionLocal()

        def run(sql, **params):
            try:
                session.execute(text(sql), params)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                print("   could not clear:", str(exc).split("\n")[0][:90])

        if created_invoices:
            run("delete from notifications where module = 'proforma_invoice' and entity_id = any(:ids)", ids=created_invoices)
            run("delete from sales_proforma_invoice_activity where proforma_invoice_id = any(:ids)", ids=created_invoices)
            run("delete from sales_proforma_invoice where id = any(:ids)", ids=created_invoices)

        if created_orders:
            run("delete from notifications where module = 'sales_order' and entity_id = any(:ids)", ids=created_orders)
            run("delete from sales_approval where document_type = 'SALES_ORDER' and document_id = any(:ids)", ids=created_orders)
            run("delete from sales_order_activity where sales_order_id = any(:ids)", ids=created_orders)
            run("delete from sales_order where id = any(:ids)", ids=created_orders)

        if created_keys:
            run("delete from sales_attachment where key = any(:keys)", keys=created_keys)

        session.commit()

        left = session.execute(
            text("select count(*) from sales_order where id = any(:ids)"),
            {"ids": created_orders or [0]},
        ).scalar()

        session.close()

        import os

        from app.routes.attachment import ATTACHMENT_DIR

        for key in created_keys:
            try:
                os.remove(ATTACHMENT_DIR / key)
            except OSError:
                pass

        print(f"   removed {len(created_orders)} orders, {removed_units} catalogue units")
        print(f"   left behind: {left}")
    except Exception as exc:  # noqa: BLE001
        print("   could not clear up:", exc)

    print(f"\n{len(passed)} passed, {len(failed)} failed")

    for name in failed:
        print(f"  FAILED  {name}")

    sys.exit(1 if failed else 0)
